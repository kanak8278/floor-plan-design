"""The furniture catalogue, parsed out of the fork's TypeScript, never retyped.

`vendor/openPlan3D/src/lib/utils/furnitureCatalog.ts` is the single source of
truth for what the editor can draw: 189 items, dimensions in **centimetres**,
some flagged `symbol: true` (2D glyph, no 3D mesh). Hand-copying that table into
Python guarantees drift the first time the fork gains an item, and a `catalogId`
the editor does not know renders as *nothing* -- a silent failure that survives
every JSON round-trip test. So we parse the TS and cache the parse as JSON,
keyed on a hash of the source, and we make a missing id raise.

Dimensions are exposed in **integer millimetres** to match `ir.Furniture`; the
cm values are kept as `*_cm` for auditing against the TS.

Orientation contract (verified against `furnitureIcons.ts`): every icon draws
its back / headboard / cistern at local -Y and its front at +Y, and
`ir.Furniture.rotation` is 0 = facing +Y. So `width` runs *along* the wall an
item backs onto and `depth` protrudes into the room. `drawFurnitureItem` does
`ctx.rotate(+angle)` in a y-down canvas, which is a rotation from +X toward +Y
in stored coordinates, i.e. facing = (-sin r, cos r). `ThreeViewer` uses
`rotation.y = -angle`, which sends local +Z to the same vector, so 2D and 3D
agree.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

_REPO = Path(__file__).resolve().parents[2]
FURNITURE_TS = _REPO / "vendor/openPlan3D/src/lib/utils/furnitureCatalog.ts"
ENTOURAGE_TS = _REPO / "vendor/openPlan3D/src/lib/utils/entourageCatalog.ts"
CACHE_PATH = Path(os.environ.get(
    "FPEVAL_CATALOG_CACHE", _REPO / "out" / "cache" / "furniture_catalog.json"))

MM_PER_CM = 10


class CatalogError(KeyError):
    """A rule referenced an id the editor cannot draw. Loud on purpose."""


@dataclass(frozen=True)
class Item:
    """One catalogue entry. `width`/`depth`/`height` are millimetres."""
    id: str
    name: str
    category: str
    icon: str
    color: str
    width: int
    depth: int
    height: int
    symbol: bool = False

    @property
    def width_cm(self) -> float: return self.width / MM_PER_CM

    @property
    def depth_cm(self) -> float: return self.depth / MM_PER_CM

    @property
    def height_cm(self) -> float: return self.height / MM_PER_CM

    @property
    def footprint_mm2(self) -> int: return self.width * self.depth


@dataclass(frozen=True)
class Entourage:
    """A 2D entourage symbol. `paths` are TS *expressions*, not path data.

    The fork generates most path strings with helpers (`cloudPath`,
    `starPath`, ...), so they cannot be read statically. We keep the metadata
    (id/name/category/width/aspect) plus the raw expression source, and we do
    not pretend to have geometry we do not have. `project.to_project` emits
    `entourage: []` anyway, so anything placed on the plan must be a real
    furniture id -- cars go in as `car_sedan`, not as `car-sedan` entourage.
    """
    id: str
    name: str
    category: str
    width: int              # mm
    aspect: float           # height/width of the symbol bbox
    path_count: int
    paths_source: str


# --------------------------------------------------------------------------
# TS parsing
# --------------------------------------------------------------------------

_ARRAY_RE = r"export const {name}[^=]*=\s*\[(?P<body>.*?)\n\];"
_STR = r"'((?:[^'\\]|\\.)*)'"


def _array_body(src: str, name: str) -> str:
    m = re.search(_ARRAY_RE.format(name=re.escape(name)), src, re.S)
    if not m:
        raise CatalogError(f"could not find `export const {name} = [...]`")
    return m.group("body")


def _split_objects(body: str) -> list[str]:
    """Top-level `{...}` chunks, brace-counting so nested arrays survive."""
    out, depth, start, in_str, esc = [], 0, -1, "", False
    for i, ch in enumerate(body):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_str:
                in_str = ""
            continue
        if ch in "'\"`":
            in_str = ch
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(body[start:i + 1])
                start = -1
    return out


def _fields(obj: str) -> dict[str, Any]:
    """Scalar `key: value` pairs at the top level of one TS object literal."""
    inner = obj.strip()[1:-1]
    out: dict[str, Any] = {}
    for m in re.finditer(r"(\w+)\s*:\s*(" + _STR + r"|-?[\d.]+|true|false)", inner):
        k, raw = m.group(1), m.group(2)
        if raw.startswith("'"):
            out[k] = raw[1:-1].replace("\\'", "'").replace("\\\\", "\\")
        elif raw in ("true", "false"):
            out[k] = raw == "true"
        else:
            out[k] = float(raw) if "." in raw else int(raw)
    return out


def _parse_furniture(src: str) -> list[dict[str, Any]]:
    items = []
    for obj in _split_objects(_array_body(src, "furnitureCatalog")):
        f = _fields(obj)
        missing = [k for k in ("id", "name", "category", "width", "depth", "height")
                   if k not in f]
        if missing:
            raise CatalogError(f"catalogue entry missing {missing}: {obj[:80]}")
        items.append({
            "id": f["id"], "name": f["name"], "category": f["category"],
            "icon": f.get("icon", ""), "color": f.get("color", "#888888"),
            "width": int(round(float(f["width"]) * MM_PER_CM)),
            "depth": int(round(float(f["depth"]) * MM_PER_CM)),
            "height": int(round(float(f["height"]) * MM_PER_CM)),
            "symbol": bool(f.get("symbol", False)),
        })
    return items


def _parse_entourage(src: str) -> list[dict[str, Any]]:
    out = []
    for obj in _split_objects(_array_body(src, "entourageCatalog")):
        f = _fields(obj)
        if "id" not in f:
            continue
        pm = re.search(r"paths\s*:\s*\[(.*?)\]\s*,?\s*$", obj.strip()[1:-1], re.S)
        psrc = (pm.group(1).strip() if pm else "")
        # count top-level commas in the paths array
        depth = n = 0
        for ch in psrc:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "," and depth == 0:
                n += 1
        out.append({
            "id": f["id"], "name": f.get("name", f["id"]),
            "category": f.get("category", "outdoor"),
            "width": int(round(float(f.get("width", 100)) * MM_PER_CM)),
            "aspect": float(f.get("aspect", 1.0)),
            "path_count": (n if psrc.rstrip().endswith(",") else n + 1) if psrc else 0,
            "paths_source": re.sub(r"\s+", " ", psrc),
        })
    return out


# --------------------------------------------------------------------------
# build + cache
# --------------------------------------------------------------------------

def _source_hash() -> str:
    h = hashlib.sha256()
    for p in (FURNITURE_TS, ENTOURAGE_TS):
        h.update(p.read_bytes() if p.exists() else b"")
    return h.hexdigest()[:16]


def build(write_cache: bool = True) -> dict[str, Any]:
    """Parse the TS and (best-effort) persist the parse as JSON."""
    if not FURNITURE_TS.exists():
        raise CatalogError(f"furniture catalogue TS not found at {FURNITURE_TS}")
    blob = {
        "source_hash": _source_hash(),
        "furniture_ts": str(FURNITURE_TS.relative_to(_REPO)),
        "furniture": _parse_furniture(FURNITURE_TS.read_text()),
        "entourage": (_parse_entourage(ENTOURAGE_TS.read_text())
                      if ENTOURAGE_TS.exists() else []),
    }
    if write_cache:
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(blob, indent=1, sort_keys=True))
        except OSError:
            pass                      # a read-only checkout must still work
    return blob


def _load_blob() -> dict[str, Any]:
    if CACHE_PATH.exists():
        try:
            blob = json.loads(CACHE_PATH.read_text())
            if blob.get("source_hash") == _source_hash() and blob.get("furniture"):
                return blob
        except (OSError, ValueError):
            pass
    return build()


_ITEMS: dict[str, Item] | None = None
_ENT: dict[str, Entourage] | None = None


def items() -> dict[str, Item]:
    """id -> Item, insertion-ordered as in the TS (so iteration is stable)."""
    global _ITEMS, _ENT
    if _ITEMS is None:
        blob = _load_blob()
        _ITEMS = {d["id"]: Item(**d) for d in blob["furniture"]}
        _ENT = {d["id"]: Entourage(**d) for d in blob["entourage"]}
    return _ITEMS


def entourage() -> dict[str, Entourage]:
    items()
    return _ENT or {}


def get(item_id: str) -> Item:
    """Lookup that raises. A bad id draws nothing in the editor, so never None."""
    try:
        return items()[item_id]
    except KeyError:
        near = [i for i in items() if item_id.split("_")[0] in i][:6]
        raise CatalogError(
            f"catalog id {item_id!r} is not in {FURNITURE_TS.name} "
            f"({len(items())} items). Nearest: {near}") from None


def has(item_id: str) -> bool:
    return item_id in items()


def by_category(category: str) -> list[Item]:
    return [i for i in items().values() if i.category == category]


def categories() -> list[str]:
    seen: list[str] = []
    for i in items().values():
        if i.category not in seen:
            seen.append(i.category)
    return seen


def require(ids: Iterable[str], where: str = "furnishing rules") -> None:
    """Fail loudly, listing every bad id at once rather than one per run."""
    bad = sorted({i for i in ids if i not in items()})
    if bad:
        raise CatalogError(f"{where} reference {len(bad)} unknown catalog ids: {bad}")


# --------------------------------------------------------------------------
# Indian gaps
# --------------------------------------------------------------------------
# The fork's catalogue is western. These items an Indian plan needs simply do
# not exist, so a rule that wants one gets the nearest real id and the swap is
# recorded here rather than invented. Format: logical name -> (catalog id, why).
SUBSTITUTIONS: dict[str, tuple[str, str]] = {
    # An IS 2556 squat pan is 500x430; the western WC is 400x650 with a cistern.
    # Footprint is the same order, the 3D mesh is wrong. Accepted for v1.
    "squat_wc":        ("toilet", "no squat pan in catalogue; western WC, 400x650"),
    # A pooja mandir unit is a 900x450x1800 shrine cabinet. `storage`
    # (Storage Cabinet, 1000x500x1200) is the closest carcass.
    "pooja_mandir":    ("storage", "no mandir; Storage Cabinet 1000x500x1200"),
    # Indian kitchens have a granite platform, not carcass units. `counter`
    # (1200x600x850) is dimensionally exact for a 600-deep platform.
    "platform":        ("counter", "counter 1200x600x850 == Indian 600 platform"),
    # Two-burner hob on a platform; `stove` is a 600x600 freestanding range.
    "hob":             ("stove", "stove 600x600 stands in for a built-in hob"),
    # A 20 kg front-load washing machine in the utility; `washer_dryer` 600x650.
    "washing_machine": ("washer_dryer", "washer_dryer 600x650, right size"),
    # Wall-hung geyser: only the 2D plumbing glyph exists.
    "geyser":          ("sym_water_heater", "2D symbol only, no 3D mesh"),
    # Indian two-wheeler: `motorcycle` 800x2100 is a fair Activa/Splendor box.
    "two_wheeler":     ("motorcycle", "motorcycle 800x2100"),
    # Hatchback (Swift 3840x1735). Catalogue has sedan 1800x4500 / SUV 1900x4800.
    "car_hatchback":   ("car_sedan", "no hatchback; sedan 1800x4500 is 17% long"),
    # Shoe rack at the foyer.
    "shoe_rack":       ("storage", "Storage Cabinet stands in for a shoe rack"),
    # Indian kitchen sink is a single-bowl 610x460 -> sink_k is 600x450. Exact.
    "kitchen_sink":    ("sink_k", "sink_k 600x450 matches a single-bowl SS sink"),
    # No dedicated water-purifier / mixer-grinder items; not modelled at all.
    "water_purifier":  ("", "MISSING: not modelled, nothing near enough"),
}

# Catalogue dimensions we deliberately override at placement time, with the
# measured reason. These are bugs in the fork's table, not style choices.
DIM_OVERRIDES: dict[str, tuple[int, int, int]] = {
    # bed_queen ships as 2000 wide x 1500 deep, i.e. a 2 m headboard and a
    # 1.5 m long bed. `drawBed` puts the headboard on the full width at -Y, so
    # the axes are swapped. An Indian queen is 1500 x 2000 (5'0" x 6'6").
    "bed_queen": (1500, 2000, 500),
    # bed_twin ships 1900 x 1000, same swap. IS single is 900 x 1900 (3'x6'3").
    "bed_twin":  (900, 1900, 500),
}


def resolve(logical: str) -> str:
    """Logical furnishing name -> real catalog id, via SUBSTITUTIONS."""
    if logical in items():
        return logical
    if logical in SUBSTITUTIONS:
        cid = SUBSTITUTIONS[logical][0]
        if not cid:
            raise CatalogError(
                f"{logical!r} is recorded MISSING: {SUBSTITUTIONS[logical][1]}")
        return cid
    return logical                    # let `get` raise with the near-miss list


def dims(item_id: str) -> tuple[int, int, int]:
    """(width, depth, height) in mm, with DIM_OVERRIDES applied."""
    it = get(item_id)
    return DIM_OVERRIDES.get(item_id, (it.width, it.depth, it.height))


def summary() -> dict[str, Any]:
    its = items()
    return {
        "n_items": len(its),
        "n_symbols": sum(1 for i in its.values() if i.symbol),
        "n_categories": len(categories()),
        "n_entourage": len(entourage()),
        "source_hash": _source_hash(),
        "cache": str(CACHE_PATH),
        "per_category": {c: len(by_category(c)) for c in categories()},
    }


if __name__ == "__main__":                              # build-time entry
    build()
    print(json.dumps(summary(), indent=2))
    for k, (cid, why) in sorted(SUBSTITUTIONS.items()):
        mark = "MISSING" if not cid else cid
        print(f"  sub {k:<16} -> {mark:<18} {why}")
    print({k: asdict(get(k)) for k in ("bed_queen", "counter", "toilet")})
