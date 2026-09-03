"""Does the IR <-> Project adapter actually carry a whole document?

`metrics.ir_identity` -- the check behind "17,000/17,000 identity" -- compares
wall geometry, opening parameters, room labels, and the plot. That is a real
result and it is not losslessness: it never looks at a second floor, a column,
a dimension string, or a wall texture, so it stayed green while the adapter
dropped all of them.

These tests come at it from the other side. The fixture is a Project with
**every field OpenPlan3D can express, on two floors**, and the assertion is
that reading and writing it changes nothing. A field that gets dropped fails
here by name.
"""
from __future__ import annotations
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from fpeval.ir import (                                        # noqa: E402
    Plan, Design, Wall, Opening, Room, Site, Stair, Furniture, Column, P,
    Presentation, GuideLine, Measurement, DimAnnotation, TextAnnotation,
    ElementGroup, EntourageItem, EntourageDef, BackgroundImage,
)
from fpeval.project import (                                   # noqa: E402
    to_project, from_project, design_from_project,
    design_round_trip_report, SIDECAR,
)
from fpeval.metrics import ir_identity                         # noqa: E402


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _rect_walls(prefix: str, w: int, h: int, thickness: int = 230) -> list[Wall]:
    return [
        Wall(f"{prefix}0", P(0, 0), P(w, 0), thickness),
        Wall(f"{prefix}1", P(w, 0), P(w, h), thickness),
        Wall(f"{prefix}2", P(w, h), P(0, h), thickness),
        Wall(f"{prefix}3", P(0, h), P(0, 0), thickness),
    ]


def _ground_storey() -> Plan:
    """A ground floor exercising every semantic field."""
    w, h = 9000, 7000
    walls = _rect_walls("w", w, h)
    # A curved wall and per-face finishes: both were silently dropped before.
    walls.append(Wall("w4", P(0, 3500), P(9000, 3500), 115,
                      curve_point=P(4500, 3900), color="#334155",
                      texture="brick", interior_color="#f8fafc",
                      interior_texture="plaster", exterior_color="#1e293b",
                      exterior_texture="render"))
    return Plan(
        id="g",
        name="Ground Floor",
        level=0,
        walls=walls,
        openings=[
            Opening("o0", "front_door", "w0", 0.25, 1050, head=2100,
                    subtype="double", swing_direction="right", flip_side=True),
            Opening("o1", "door", "w4", 0.60, 900, head=2100, subtype="sliding"),
            Opening("o2", "window", "w1", 0.40, 1500, sill=900, head=2400,
                    subtype="bay"),
        ],
        rooms=[
            Room("r0", "Living", "living", ["w0", "w3", "w4"],
                 [P(0, 0), P(9000, 0), P(9000, 3500), P(0, 3500)], 31_500_000,
                 room_class="indoor", floor_texture="marble-white",
                 color="#fef3c7", label_offset=P(200, -300),
                 anchor=P(4500, 1750)),
            Room("r1", "Sit Out", "sitout", ["w2", "w4"],
                 [P(0, 3500), P(9000, 3500), P(9000, 7000), P(0, 7000)],
                 31_500_000),
        ],
        site=Site(plot_polygon=[P(-1000, -1000), P(10000, -1000),
                                P(10000, 8000), P(-1000, 8000)],
                  north_deg=112.5, setbacks_mm={"front": 3000, "rear": 1500}),
        stairs=[Stair("s0", P(7500, 5500), rotation=90.0, width=1200, depth=2800,
                      riser_count=16, direction="up", stair_type="l-shaped",
                      room_id="r1")],
        furniture=[
            Furniture("f0", "sofa", P(2000, 1500), rotation=180.0, width=2000,
                      depth=900, height=800, room_id="r0", locked=True,
                      color="#7c3aed", material="fabric",
                      scale_x=1.1, scale_y=1.0, scale_z=0.95),
            Furniture("f1", "coffee_table", P(2000, 2600), room_id="r0"),
        ],
        columns=[Column("c0", P(4500, 3500), rotation=45.0, shape="square",
                        size=350, height=3000, color="#111827")],
        storey_height=3050,
        provenance={"source": "roundtrip-fixture", "seed": 7},
        presentation=Presentation(
            guides=[GuideLine("gd0", "vertical", 4500),
                    GuideLine("gd1", "horizontal", 3500)],
            measurements=[Measurement("m0", P(0, 0), P(9000, 0))],
            dimensions=[DimAnnotation("a0", P(0, 0), P(9000, 0), offset=600,
                                      label="9.00 m clear")],
            texts=[TextAnnotation("t0", P(4500, 200), "GROUND FLOOR PLAN",
                                  font_size=22, color="#0f172a", rotation=0.0)],
            groups=[ElementGroup("grp0", ["f0", "f1"])],
            entourage=[EntourageItem("e0", "car-sedan", P(-500, 6000), 4200,
                                     rotation=15.0, opacity=0.8, locked=True)],
            background=BackgroundImage("data:image/png;base64,AAAA",
                                       position=P(-1000, -1000), scale=1.4,
                                       opacity=0.35, rotation=2.5, locked=True),
        ),
    )


def _first_storey() -> Plan:
    """A first floor. The old adapter never read this at all."""
    return Plan(
        id="f1",
        name="First Floor",
        level=1,
        walls=_rect_walls("u", 9000, 7000),
        openings=[Opening("o0", "window", "u2", 0.5, 1200, sill=900, head=2100)],
        rooms=[Room("r0", "Master Bedroom", "master_bedroom",
                    ["u0", "u1", "u2", "u3"],
                    [P(0, 0), P(9000, 0), P(9000, 7000), P(0, 7000)],
                    63_000_000)],
        site=Site(north_deg=112.5),
        columns=[Column("c0", P(4500, 3500))],
        storey_height=2900,
        presentation=Presentation(
            texts=[TextAnnotation("t0", P(4500, 200), "FIRST FLOOR PLAN")]),
    )


def _full_design() -> Design:
    return Design(
        id="rt",
        storeys=[_ground_storey(), _first_storey()],
        active_storey_id="g",
        name="Round-trip fixture",
        description="Every field OpenPlan3D can express, on two floors.",
        custom_entourage=[EntourageDef("car-sedan", "Sedan",
                                       "data:image/png;base64,BBBB", 0.42)],
        created_at="2026-09-04T00:00:00.000Z",
        updated_at="2026-09-04T01:00:00.000Z",
        provenance={"fixture": True},
    )


# --------------------------------------------------------------------------
# the claim
# --------------------------------------------------------------------------

def test_full_design_round_trip_is_idempotent():
    proj = to_project(_full_design())
    report = design_round_trip_report(proj)
    assert report["idempotent"], report["diffs"]
    assert report["floors_in"] == report["floors_out"] == 2


def test_every_storey_survives():
    """The specific defect: `from_project` read only `activeFloorId`."""
    proj = to_project(_full_design())
    back = design_from_project(proj)
    assert [s.id for s in back.storeys] == ["g", "f1"]
    assert [s.level for s in back.storeys] == [0, 1]
    assert [s.name for s in back.storeys] == ["Ground Floor", "First Floor"]
    assert back.active_storey_id == "g"
    # Per-storey height is not a Project field and has to survive the sidecar.
    assert [s.storey_height for s in back.storeys] == [3050, 2900]


def test_design_survives_field_by_field():
    """Compare the whole dataclass tree, not a chosen subset of it."""
    original = _full_design()
    back = design_from_project(to_project(original))
    assert back.to_dict() == original.to_dict()


@pytest.mark.parametrize("attr,getter", [
    ("columns", lambda s: [(c.id, c.position.as_tuple(), c.shape, c.size,
                            c.rotation, c.color) for c in s.columns]),
    ("guides", lambda s: [(g.id, g.orientation, g.position)
                          for g in s.presentation.guides]),
    ("measurements", lambda s: [(m.id, m.start.as_tuple(), m.end.as_tuple())
                                for m in s.presentation.measurements]),
    ("dimensions", lambda s: [(d.id, d.offset, d.label)
                              for d in s.presentation.dimensions]),
    ("texts", lambda s: [(t.id, t.text, t.font_size, t.color)
                         for t in s.presentation.texts]),
    ("groups", lambda s: [(g.id, tuple(g.element_ids))
                          for g in s.presentation.groups]),
    ("entourage", lambda s: [(e.id, e.def_id, e.width, e.opacity, e.locked)
                             for e in s.presentation.entourage]),
    ("background", lambda s: (s.presentation.background.data_url,
                              s.presentation.background.scale,
                              s.presentation.background.locked)),
    ("wall_finishes", lambda s: [(w.id, w.color, w.texture, w.interior_color,
                                  w.exterior_texture) for w in s.walls]),
    ("curve_point", lambda s: [w.curve_point for w in s.walls]),
    ("opening_subtypes", lambda s: [(o.id, o.kind, o.subtype, o.swing_direction,
                                     o.flip_side, o.sill, o.head)
                                    for o in s.openings]),
    ("room_style", lambda s: [(r.id, r.room_class, r.floor_texture, r.color,
                               r.label_offset) for r in s.rooms]),
    ("room_anchor", lambda s: [(r.id, r.anchor) for r in s.rooms]),
    ("room_area_exact", lambda s: [r.area for r in s.rooms]),
    ("furniture_extras", lambda s: [(f.id, f.color, f.material, f.scale_x,
                                     f.scale_y, f.scale_z, f.locked)
                                    for f in s.furniture]),
    ("setbacks", lambda s: s.site.setbacks_mm),
])
def test_named_field_survives(attr, getter):
    """One test per field family, so a regression names what it broke."""
    original = _full_design()
    back = design_from_project(to_project(original))
    assert getter(back.storeys[0]) == getter(original.storeys[0]), attr


def test_design_level_fields_survive():
    original = _full_design()
    back = design_from_project(to_project(original))
    assert back.name == original.name
    assert back.description == original.description
    assert back.created_at == original.created_at
    assert back.updated_at == original.updated_at
    assert back.provenance == original.provenance
    assert [(d.id, d.name, d.data_url, d.aspect) for d in back.custom_entourage] \
        == [(d.id, d.name, d.data_url, d.aspect)
            for d in original.custom_entourage]


# --------------------------------------------------------------------------
# the old guarantee still holds
# --------------------------------------------------------------------------

def test_ir_identity_still_exact():
    """Whatever else changed, the metric the corpus run reports must not move."""
    plan = _ground_storey()
    back = from_project(to_project(plan))
    ident = ir_identity(plan, back)
    assert ident["walls_equal"] and ident["openings_equal"]
    assert ident["rooms_equal"] and ident["plot_equal"]


def test_bare_plan_still_accepted():
    """Every existing caller passes a `Plan`, not a `Design`."""
    plan = _ground_storey()
    proj = to_project(plan, name="explicit name")
    assert proj["name"] == "explicit name"
    assert len(proj["floors"]) == 1
    assert from_project(proj).to_dict() == plan.to_dict()


def test_generated_plan_keeps_its_default_styling():
    """A plan built in Python must not acquire editor defaults on the way out.

    Emitting a derived floor texture and then reading it back as an *explicit*
    one would make the second pass differ from the first for every plan the
    solver has ever produced.
    """
    plan = Plan(id="p", walls=_rect_walls("w", 4000, 3000),
                rooms=[Room("r0", "Bath", "bathroom", ["w0"], [], 12_000_000)])
    proj = to_project(plan)
    assert proj["floors"][0]["rooms"][0]["floorTexture"] == "ceramic-white"
    back = from_project(proj)
    assert back.rooms[0].floor_texture == ""      # still derived, not baked in
    assert back.rooms[0].room_class == ""
    assert back.rooms[0].category == "bathroom"


# --------------------------------------------------------------------------
# quantisation, stated honestly
# --------------------------------------------------------------------------

def test_sub_millimetre_input_snaps_once_then_holds():
    """`Project` is float cm; the IR is integer mm. The claim is idempotence
    after one pass, not that arbitrary floats survive."""
    proj = to_project(_ground_storey())
    # 12.34 cm = 123.4 mm -> 123 mm. A drag can produce this; the editor's own
    # snap grid is 25 cm, but snapping can be switched off.
    proj["floors"][0]["walls"][0]["start"] = {"x": 12.34, "y": -0.06}
    first = design_from_project(proj)
    assert first.storeys[0].walls[0].start.as_tuple() == (123, -1)
    # Second pass changes nothing.
    report = design_round_trip_report(proj)
    assert report["idempotent"], report["diffs"]


# --------------------------------------------------------------------------
# projects written before the sidecar grew up
# --------------------------------------------------------------------------

def test_version_1_sidecar_still_reads():
    """`localStorage`, `tests/eval_corpus.json`, and the gallery's
    `projects.json` all hold version-1 Project JSON. It has to keep loading."""
    proj = to_project(_ground_storey())
    side = proj[SIDECAR]
    v1 = copy.deepcopy(proj)
    v1[SIDECAR] = {k: v for k, v in side.items()
                   if k not in ("version", "floors", "design_id")}
    back = from_project(v1)
    # Categories, plot, north, and opening kinds all come from the flat keys.
    assert [r.category for r in back.rooms] == ["living", "sitout"]
    assert back.site.north_deg == 112.5
    assert len(back.site.plot_polygon) == 4
    assert [o.kind for o in back.openings] == ["front_door", "door", "window"]
    # Room polygons were never in a v1 sidecar, so they get re-derived from the
    # wall graph rather than coming back empty.
    assert all(len(r.polygon) >= 3 for r in back.rooms)


def test_v1_project_reports_its_loss_rather_than_hiding_it():
    """A v1 project genuinely cannot round-trip: exact areas, setbacks, and
    per-storey heights were never written. The report must say so."""
    proj = to_project(_ground_storey())
    v1 = copy.deepcopy(proj)
    v1[SIDECAR] = {k: v for k, v in proj[SIDECAR].items()
                   if k not in ("version", "floors", "design_id")}
    report = design_round_trip_report(v1)
    # Second pass is stable (it is v2 by then) even though the first pass lost
    # data -- which is exactly why idempotence is checked on passes 2 and 3.
    assert report["idempotent"], report["diffs"]
    assert design_from_project(v1).storeys[0].site.setbacks_mm == {}
    assert _ground_storey().site.setbacks_mm != {}
