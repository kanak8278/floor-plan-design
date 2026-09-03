"""Prompt suite: examples with machine-checkable ground truth.

Every assertion here must be verifiable without a human and without a reference
plan. That constraint is deliberate: there is no single correct answer to "3BHK on
a 30x40", so scoring geometric similarity to one reference would penalise good
alternatives. Instead we check
  * spec recovery  -- did the LLM read the brief correctly?
  * feasibility    -- did the system solve, or correctly refuse?
  * compliance     -- zero rules-engine errors
  * programme fit  -- are the rooms the brief asked for actually present?
  * vastu          -- are the named zones respected?

`expect: clarify` covers underdetermined prompts, where inventing a value is the
failure and asking is the pass.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
import json

from . import roomtypes as rt

Outcome = Literal["optimal", "feasible", "infeasible", "clarify"]
SUITE_DIR = Path(__file__).resolve().parents[2] / "suite"

# Feature tags an example can exercise. Kept closed so a typo is an error rather
# than a silently uncovered feature.
FEATURES = {
    "plot", "bhk", "facing", "vastu", "pooja", "parking", "gate", "compound_wall",
    "garden", "sitout", "balcony", "utility", "attached_bath", "common_bath",
    "multifloor", "stairs", "duplex", "study", "servant", "store", "shaft",
    "furniture", "kitchen_layout", "dining", "hall", "corner_plot", "irregular_plot",
    "apartment_unit", "area_quote", "setbacks", "far", "coverage", "budget",
    "family", "underdetermined", "contradictory", "rwh", "terrace", "porch",
}


@dataclass
class Truth:
    """Ground truth for one example. Every field is optional; only what the
    prompt actually determines should be asserted."""
    site_kind: str | None = None
    plot_width_ft: float | None = None
    plot_depth_ft: float | None = None
    road_facing: str | None = None
    corner_plot: bool | None = None
    storeys: int | None = None
    half_bhk: bool | None = None
    # room type -> exact count required in the recovered programme
    rooms: dict[str, int] = field(default_factory=dict)
    # room types that must appear at least once (weaker than `rooms`)
    rooms_min: dict[str, int] = field(default_factory=dict)
    attached_bath: int | None = None
    vastu_enabled: bool | None = None
    vastu_strictness: str | None = None
    vastu_zones: dict[str, str] = field(default_factory=dict)
    entrance_side: str | None = None
    # furnishing / outdoor items that must be placed (catalogue ids)
    must_place: list[str] = field(default_factory=list)
    # Room-type pairs that must end up with a door between them. Needed for
    # detailed briefs: "a balcony off the master bedroom" and "a balcony off the
    # living room" are different plans, and nothing else in Truth could say so.
    adjacent: list[list[str]] = field(default_factory=list)
    # Pairs that must NOT have a door between them.
    not_adjacent: list[list[str]] = field(default_factory=list)
    # Which room each item belongs in: {"washer_dryer": "utility"}
    place_in: dict[str, str] = field(default_factory=dict)
    area_quote_sqft: dict[str, float] = field(default_factory=dict)
    coverage_max: float | None = None
    far_max: float | None = None
    family: str | None = None          # e.g. 'couple', 'couple + 2 kids + parents'
    budget_band: str | None = None     # e.g. 'economy', 'premium', '40-50L'
    unit_label: str | None = None      # verbatim market label, e.g. '3 BHK + 2 T'
    notes: str = ""


@dataclass
class Example:
    id: str
    prompt: str
    features: list[str]
    expect: Outcome
    truth: Truth = field(default_factory=Truth)
    expect_reason: str | None = None      # for infeasible: which group binds
    clarify_about: list[str] = field(default_factory=list)  # what must be asked
    source: str = "authored"
    # Which tracks an example is meaningful on. Some assertions live purely in
    # the prompt (a quoted built-up area, a semantic contradiction) and Truth has
    # no field to carry them, so track A would score them as false passes.
    tracks: tuple[str, ...] = ("A", "B")
    note: str = ""

    def validate(self) -> list[str]:
        errs = []
        bad = [f for f in self.features if f not in FEATURES]
        if bad:
            errs.append(f"{self.id}: unknown feature tags {bad}")
        for k in list(self.truth.rooms) + list(self.truth.rooms_min):
            if k not in rt.T:
                errs.append(f"{self.id}: unknown room type '{k}'")
        for pair in list(self.truth.adjacent) + list(self.truth.not_adjacent):
            if len(pair) != 2:
                errs.append(f"{self.id}: adjacency needs exactly two room types, got {pair}")
                continue
            for k in pair:
                if k not in rt.T:
                    errs.append(f"{self.id}: adjacency on unknown room type '{k}'")
        for k in self.truth.place_in.values():
            if k not in rt.T:
                errs.append(f"{self.id}: place_in targets unknown room type '{k}'")
        for k, z in self.truth.vastu_zones.items():
            if k not in rt.T:
                errs.append(f"{self.id}: vastu zone on unknown room type '{k}'")
            if z not in ("N", "NE", "E", "SE", "S", "SW", "W", "NW", "centre"):
                errs.append(f"{self.id}: bad zone '{z}'")
        if self.expect == "infeasible" and not self.expect_reason:
            errs.append(f"{self.id}: infeasible example needs expect_reason")
        if self.expect == "clarify" and not self.clarify_about:
            errs.append(f"{self.id}: clarify example must say what to ask about")
        if self.expect != "clarify" and self.truth.site_kind == "plot" \
           and (self.truth.plot_width_ft is None) != (self.truth.plot_depth_ft is None):
            errs.append(f"{self.id}: plot dims must be given as a pair or not at all")
        return errs


def _truth(d: dict[str, Any]) -> Truth:
    known = {f for f in Truth.__dataclass_fields__}
    unknown = set(d) - known
    if unknown:
        raise ValueError(f"unknown truth fields: {sorted(unknown)}")
    return Truth(**d)


def load_file(path: Path) -> list[Example]:
    raw = json.loads(path.read_text())
    # The suite directory also holds SELECTION.json and similar metadata; a file
    # with no `examples` key is not an example file.
    if "examples" not in raw:
        return []
    out = []
    for e in raw["examples"]:
        out.append(Example(
            id=e["id"], prompt=e["prompt"], features=e["features"],
            expect=e["expect"], truth=_truth(e.get("truth", {})),
            expect_reason=e.get("expect_reason"),
            clarify_about=e.get("clarify_about", []),
            source=e.get("source", raw.get("source", "authored")),
            tracks=tuple(e.get("tracks", ("A", "B"))),
            note=e.get("note", ""),
        ))
    return out


def load_suite(directory: Path | None = None) -> list[Example]:
    d = directory or SUITE_DIR
    exs: list[Example] = []
    for p in sorted(d.glob("*.json")):
        exs.extend(load_file(p))
    ids = [e.id for e in exs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate example ids: {sorted(dupes)}")
    return exs


def validate_suite(exs: list[Example]) -> list[str]:
    errs: list[str] = []
    for e in exs:
        errs.extend(e.validate())
    return errs


def coverage(exs: list[Example]) -> dict[str, int]:
    c = {f: 0 for f in sorted(FEATURES)}
    for e in exs:
        for f in e.features:
            if f in c:
                c[f] += 1
    return c
