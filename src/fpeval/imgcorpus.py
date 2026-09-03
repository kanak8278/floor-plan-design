"""Verification for floor-plan schedules extracted from images.

Why this can work at all: Indian architectural plans annotate each room in BOTH
millimetres and feet-inches (e.g. "4000X3500" above "13'1\"X11'6\""). Those are two
independent encodings of the same measurement, so they cross-check each other with
no external ground truth required.

The checks below catch three distinct error classes:
  * extraction errors (model misread a digit)
  * drafting errors in the SOURCE drawing (free plans genuinely contain them)
  * label/room misassignment (only caught by the pixel-geometry check, separate)

A plan enters the corpus only if it passes. Trusting these drawings blindly would
poison the eval set, which is worse than having no eval set.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

MM_PER_INCH = 25.4
MM_PER_FT = 304.8

# Indian plans annotate dimensions in several forms and we must accept all of them.
# Found in the wild: "4000X3500" (mm, house-plan sites), "3.84m x 3.81m" (metres,
# builder brochures), "2.45mx2.74m", and feet-inches "12'7\"x12'6\"".
# Reading only the mm form silently discarded every builder plan -- the data was
# there, the parser was not.
_MM_PAIR = re.compile(r"(\d{3,5})\s*(?:mm)?\s*[xX×*]\s*(\d{3,5})\s*(?:mm)?", re.I)
_M_PAIR  = re.compile(r"(\d{1,2}(?:\.\d{1,3})?)\s*m\s*[xX×*]?\s*(\d{1,2}(?:\.\d{1,3})?)\s*m", re.I)
_CM_PAIR = re.compile(r"(\d{2,4})\s*cm\s*[xX×*]\s*(\d{2,4})\s*cm", re.I)
# 13'3"X17'3"  |  13'-3" x 17'-3"  |  12'7"x12'6"
_FT_RE = re.compile(r"""(\d{1,3})\s*['\u2019]\s*-?\s*(\d{1,2})?\s*["\u201d]?\s*[xX×*]\s*
                        (\d{1,3})\s*['\u2019]\s*-?\s*(\d{1,2})?\s*["\u201d]?""", re.X)
_WIDE_M  = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*m\s*wide", re.I)
_WIDE_MM = re.compile(r"(\d{3,5})\s*(?:mm)?\s*wide", re.I)


def parse_mm(s: str | None) -> tuple[int, int] | None:
    """Parse a printed dimension pair to (a_mm, b_mm), whatever unit it used."""
    if not s:
        return None
    t = str(s).strip()
    m = _M_PAIR.search(t)
    if m:
        a, b = float(m.group(1)) * 1000, float(m.group(2)) * 1000
        if 200 <= a <= 60000 and 200 <= b <= 60000:
            return (round(a), round(b))
    m = _CM_PAIR.search(t)
    if m:
        return (int(m.group(1)) * 10, int(m.group(2)) * 10)
    m = _MM_PAIR.search(t)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def parse_width_mm(s: str | None) -> int | None:
    """Corridors are often annotated width-only: "1.15m WIDE" / "900 WIDE"."""
    if not s:
        return None
    m = _WIDE_M.search(str(s))
    if m:
        return round(float(m.group(1)) * 1000)
    m = _WIDE_MM.search(str(s))
    return int(m.group(1)) if m else None


def parse_ft(s: str | None) -> tuple[float, float] | None:
    """Return (a_mm, b_mm) from a printed feet-inches pair."""
    if not s: return None
    m = _FT_RE.search(s)
    if not m: return None
    def v(ft, inch): return int(ft) * MM_PER_FT + (int(inch) if inch else 0) * MM_PER_INCH
    return (v(m.group(1), m.group(2)), v(m.group(3), m.group(4)))


@dataclass
class RoomCheck:
    name: str
    mm: tuple[int, int] | None
    ft_mm: tuple[float, float] | None
    status: str                      # ok | mismatch | mm_only | ft_only | unparsed
    worst_err_mm: float | None = None
    worst_err_pct: float | None = None
    detail: str = ""


@dataclass
class PlanVerdict:
    accepted: bool
    rooms: list[RoomCheck] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def check_dual_unit(rooms: list[dict], tol_mm: float = 60.0,
                    tol_pct: float = 0.03) -> list[RoomCheck]:
    """A printed pair must agree to within a drafting rounding tolerance.

    tol_mm=60 because feet-inch labels are rounded to the nearest inch (25.4 mm)
    on each of two axes, so ~2 inches of legitimate slack exists.
    """
    out = []
    for r in rooms:
        nm = r.get("name", "?")
        mm, ft = parse_mm(r.get("dim_mm")), parse_ft(r.get("dim_ft"))
        if mm and ft:
            errs = [abs(a - b) for a, b in zip(mm, ft)]
            pcts = [e / a for e, a in zip(errs, mm)]
            i = max(range(2), key=lambda k: pcts[k])
            ok = all(e <= tol_mm or p <= tol_pct for e, p in zip(errs, pcts))
            out.append(RoomCheck(nm, mm, ft, "ok" if ok else "mismatch",
                                 errs[i], pcts[i],
                                 "" if ok else f"{mm} mm vs {tuple(round(x) for x in ft)} mm from feet"))
        elif mm:   out.append(RoomCheck(nm, mm, None, "mm_only"))
        elif ft:   out.append(RoomCheck(nm, None, ft, "ft_only"))
        else:
            raw = (r.get("dim_mm"), r.get("dim_ft"))
            out.append(RoomCheck(nm, None, None,
                                 "unparsed" if any(raw) else "mm_only",
                                 detail=f"raw={raw}" if any(raw) else "no printed dimension"))
    return out


def verify(extraction: dict, *, min_dual_checked: int = 4,
           max_mismatch_frac: float = 0.25) -> PlanVerdict:
    rooms = extraction.get("rooms", [])
    checks = check_dual_unit(rooms)
    dual = [c for c in checks if c.status in ("ok", "mismatch")]
    bad = [c for c in dual if c.status == "mismatch"]
    reasons = []

    if len(dual) < min_dual_checked:
        reasons.append(f"only {len(dual)} rooms carry both units "
                       f"(need {min_dual_checked}) — nothing to cross-check against")
    if dual and len(bad) / len(dual) > max_mismatch_frac:
        reasons.append(f"{len(bad)}/{len(dual)} rooms fail the mm-vs-feet cross-check")

    plot = extraction.get("plot") or {}
    if not (plot.get("width_ft") and plot.get("depth_ft")):
        reasons.append("no printed plot dimensions — cannot anchor scale or check bye-laws")

    # Area closure: labelled interior area must fit inside the plot footprint.
    plot_mm2 = None
    if plot.get("width_ft") and plot.get("depth_ft"):
        plot_mm2 = plot["width_ft"] * MM_PER_FT * plot["depth_ft"] * MM_PER_FT
    room_mm2 = sum(c.mm[0] * c.mm[1] for c in checks if c.mm)
    if plot_mm2 and room_mm2 > plot_mm2:
        reasons.append(f"labelled room area {room_mm2/1e6:.1f} m² exceeds plot "
                       f"{plot_mm2/1e6:.1f} m² — labels or plot size are wrong")

    return PlanVerdict(
        accepted=not reasons, rooms=checks, reasons=reasons,
        stats={"n_rooms": len(rooms), "n_dual_checked": len(dual), "n_mismatch": len(bad),
               "room_area_m2": round(room_mm2 / 1e6, 1) if room_mm2 else None,
               "plot_area_m2": round(plot_mm2 / 1e6, 1) if plot_mm2 else None,
               "coverage": round(room_mm2 / plot_mm2, 3) if (plot_mm2 and room_mm2) else None},
    )

def parse_any(s: str | None) -> tuple[int, int] | None:
    """Parse a dimension pair in ANY printed unit, metric or imperial.

    Needed because a large share of builder plans are feet-inches ONLY -- e.g.
    Divyasree Shettigere prints "MASTER BEDROOM 12'0\" X 13'0\"" with no metric
    second unit and a footer stating "All dimensions are in feet and inches".
    Testing only `parse_mm` recorded those plans as having no dimensions at all.
    """
    ft = parse_ft(s)
    if ft:
        return (round(ft[0]), round(ft[1]))
    return parse_mm(s)


def verification_tier(rooms: list[dict], areas: dict | None) -> str:
    """How strongly can this plan be checked?

      A  dual units + a printed area figure   -> two independent checksums
      B  single unit + a printed area figure  -> one independent checksum
      C  neither                              -> unverifiable, must be refused

    Tier B is genuinely usable; treating it as unverifiable discarded most of the
    builder corpus.
    """
    dual = sum(1 for r in rooms
               if parse_mm(r.get("dim_primary")) and parse_ft(r.get("dim_secondary")))
    any_dim = sum(1 for r in rooms
                  if parse_any(r.get("dim_primary")) or parse_any(r.get("dim_secondary")))
    a = areas or {}
    has_area = any(a.get(k) for k in ("carpet_sqft", "carpet_sqm", "rera_carpet_sqft",
                                      "built_up_sqft", "super_built_up_sqft",
                                      "super_built_up_sqm"))
    if dual >= 4 and has_area:
        return "A"
    if dual >= 4:
        return "A-"          # dual units but no area total
    if any_dim >= 4 and has_area:
        return "B"
    return "C"
