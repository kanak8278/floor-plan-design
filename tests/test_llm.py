"""Tests for the LLM layer.

Two tiers, deliberately separated.

**Offline** (the default): schema shape, `DesignSpec.validate()`, patch-op
parsing/serialisation/validation, brief generation and the absolute-dimension
guard. No API key, no network, runs in under a second. These are the tests that
protect the contract.

**API** (opt-in): real `extract_spec` runs over >=20 realistic Indian prompts,
the round-trip probe, and a `propose_patch` smoke test. Gated on
`FPEVAL_LLM_API=1` so CI does not spend money, and marked `api` so
`-m "not api"` works too.

Run offline:   uv run --with pytest --with shapely --with numpy pytest tests/test_llm.py
Run with API:  FPEVAL_LLM_API=1 uv run --with pytest --with shapely --with numpy \
                   --with anthropic pytest tests/test_llm.py -s
Report only:   FPEVAL_LLM_API=1 ... python tests/test_llm.py   # prints the eval table
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from fpeval.spec import (  # noqa: E402
    ROOM_CATEGORIES, BEDROOM_CATEGORIES, SIDES, ZONES,
    Adjacency, AreaQuote, DesignSpec, EntranceSpec, RoomSpec, VastuSpec,
    bhk_programme, default_indian_spec,
)
from fpeval import llm as L  # noqa: E402
from fpeval import brief as B  # noqa: E402

API = os.environ.get("FPEVAL_LLM_API") == "1"
requires_api = pytest.mark.skipif(not API, reason="set FPEVAL_LLM_API=1 to spend money")


# ==========================================================================
# The prompt suite. 24 prompts: 18 determined, 6 deliberately underdetermined.
# ==========================================================================

PROMPTS: list[dict] = [
    # ---------------- fully determined ----------------
    dict(id="p01", text="30 by 40 site, east facing, Bengaluru. Need 3BHK with "
                        "pooja room and covered car parking. G+1.",
         plot=(30, 40), facing="east", beds=3, storeys=2, vastu=None,
         cats={"pooja", "parking", "staircase"}),
    dict(id="p02", text="We have a 20x30 north facing plot in Chennai. 2BHK only, "
                        "single floor, tight budget around 18 lakhs.",
         plot=(20, 30), facing="north", beds=2, storeys=1, vastu=None, cats=set()),
    dict(id="p03", text="40x60 west facing site in Hyderabad. Want a 4BHK duplex, "
                        "vastu compliant, with utility, sit-out and parking for two cars.",
         plot=(40, 60), facing="west", beds=4, storeys=2, vastu=True,
         cats={"utility", "sit_out", "parking", "staircase"}),
    dict(id="p04", text="Plot is 30 x 50 facing south, Pune. 3BHK G+1 with a "
                        "separate dining and a small study. Master bedroom with "
                        "attached bathroom.",
         plot=(30, 50), facing="south", beds=3, storeys=2, vastu=None,
         cats={"dining", "study", "staircase"}),
    dict(id="p05", text="50 by 80 east facing, Kochi. 4BHK G+2, pooja room in the "
                        "north-east as per vastu, servant room, car porch.",
         plot=(50, 80), facing="east", beds=4, storeys=3, vastu=True,
         cats={"pooja", "servant", "staircase"}),
    dict(id="p06", text="need plan for 25*40 site north facing, 2bhk, puja, wash "
                        "area, 2 wheeler parking. budget 25 lakhs.",
         plot=(25, 40), facing="north", beds=2, storeys=1, vastu=None,
         cats={"pooja", "utility", "parking"}),
    dict(id="p07", text="Our 30x40 plot in Bengaluru is east facing. Joint family "
                        "- us, two kids and my parents. We need 3BHK, one bedroom "
                        "on the ground floor because my mother cannot climb "
                        "stairs. G+1.",
         plot=(30, 40), facing="east", beds=3, storeys=2, vastu=None,
         cats={"staircase"}),
    dict(id="p08", text="40 by 40 corner plot, road on north and east, Jaipur. "
                        "3BHK single floor with a big hall, pooja and store room.",
         plot=(40, 40), facing=None, beds=3, storeys=1, vastu=None,
         cats={"pooja", "store"}),
    dict(id="p09", text="Strictly as per vastu. 30x40 north facing site in "
                        "Ahmedabad. 3BHK, kitchen in the south-east, master "
                        "bedroom south-west, no toilet in the north-east. G+1.",
         plot=(30, 40), facing="north", beds=3, storeys=2, vastu=True,
         cats={"staircase"}),
    dict(id="p10", text="30 by 30 site west facing, Coimbatore. 2BHK ground floor "
                        "only. Please keep a utility behind the kitchen and no "
                        "toilet next to the kitchen.",
         plot=(30, 30), facing="west", beds=2, storeys=1, vastu=None,
         cats={"utility"}),
    dict(id="p11", text="Looking at a 25 by 50 east facing site in Lucknow for a "
                        "3BHK duplex. Ground floor should have the parking, hall "
                        "and kitchen; bedrooms upstairs.",
         plot=(25, 50), facing="east", beds=3, storeys=2, vastu=None,
         cats={"parking", "staircase"}),
    dict(id="p12", text="60x40 plot, south facing, Delhi. 4BHK with two attached "
                        "bathrooms, a common toilet, dining, pooja and a home "
                        "office. Single storey is fine.",
         plot=(60, 40), facing="south", beds=4, storeys=1, vastu=None,
         cats={"pooja", "dining", "office", "toilet"}),
    dict(id="p13", text="my site is 30 by 40, facing east, bangalore. want 3 bhk "
                        "with pooja. vastu compliant if possible but not strict.",
         plot=(30, 40), facing="east", beds=3, storeys=None, vastu=True,
         cats={"pooja"}),
    dict(id="p14", text="1200 sqft plot, 30 ft road frontage facing west, "
                        "Kolkata. 3BHK G+1, sit out in front, utility at the back.",
         plot=(30, 40), facing="west", beds=3, storeys=2, vastu=None,
         cats={"sit_out", "utility", "staircase"}),
    dict(id="p15", text="30x50 north facing site. Need 3BHK where the master "
                        "bedroom is at least 12x14 and every bedroom has an "
                        "attached bathroom. G+1 with a terrace.",
         plot=(30, 50), facing="north", beds=3, storeys=2, vastu=None,
         cats={"staircase"}),
    dict(id="p16", text="Retired couple, 20 by 30 east facing plot in Chennai. "
                        "1BHK ground floor with a pooja space and a small sit-out. "
                        "Nothing fancy.",
         plot=(20, 30), facing="east", beds=1, storeys=1, vastu=None,
         cats={"pooja", "sit_out"}),
    # beds is deliberately unlabelled: "4BHK G+1, each floor a separate unit"
    # is genuinely ambiguous between 4 total and 4 per floor. This started as a
    # beds=4 label, the model read it as 8 and asked which -- the label was
    # wrong, not the model. Left ambiguous so the suite does not encode my error.
    dict(id="p17", text="40 x 60 site facing north, Hyderabad, budget 1 crore. "
                        "4BHK G+1, each floor a separate unit for my two sons, "
                        "common staircase outside.",
         plot=(40, 60), facing="north", beds=None, storeys=2, vastu=None,
         cats={"staircase"}),
    dict(id="p18", text="30ft x 40ft, east facing, Bengaluru. 3BHK. We want the "
                        "toilet NOT visible from the main door and the kitchen "
                        "away from the entrance.",
         plot=(30, 40), facing="east", beds=3, storeys=None, vastu=None,
         cats=set()),
    # ---------------- apartment units: NO plot exists ----------------
    dict(id="a01", text="We have booked a 3 BHK + 2 T - TYPE 3 G unit in a "
                        "tower in Bengaluru, east facing. Builder quotes 1450 "
                        "sq ft saleable, 990 sq ft carpet, 940 sq ft RERA "
                        "carpet. Please re-plan the interior: we want a PUJA, "
                        "UTILITY and a PDR near the foyer.",
         plot=None, facing="east", beds=3, storeys=1, vastu=None,
         cats={"pooja", "utility", "powder"}, site_kind="apartment_unit",
         no_plot_expected=True),
    dict(id="a02", text="2BHK 2T TYPE C18 flat, 1150 sq ft saleable and 785 sq "
                        "ft carpet, north facing, Pune. Need a utility with "
                        "handwash and two balconies.",
         plot=None, facing="north", beds=2, storeys=1, vastu=None,
         cats={"utility", "balcony"}, site_kind="apartment_unit",
         no_plot_expected=True),
    dict(id="a03", text="Looking at a 3.5 BHK unit on the 9th floor in "
                        "Hyderabad, west facing, 1820 sq ft super built-up. "
                        "Vastu compliant layout please.",
         plot=None, facing="west", beds=3, storeys=1, vastu=True,
         cats=set(), site_kind="apartment_unit", no_plot_expected=True),
    dict(id="a04", text="4BHK + SR + ST + PDR apartment in Chennai, saleable "
                        "2400 sq ft, south facing. Servant room needs its own "
                        "toilet.",
         plot=None, facing="south", beds=4, storeys=1, vastu=None,
         cats={"servant", "store", "powder"}, site_kind="apartment_unit",
         no_plot_expected=True),
    # ---------------- deliberately underdetermined ----------------
    # These must produce clarifying questions and NOT an invented plot size.
    dict(id="u01", text="I want a 3BHK house with a pooja room. Vastu compliant.",
         plot=None, facing=None, beds=3, storeys=None, vastu=True,
         cats={"pooja"}, underdetermined=["plot", "facing"]),
    dict(id="u02", text="We need a duplex for a family of five in Bengaluru.",
         plot=None, facing=None, beds=None, storeys=2, vastu=None,
         cats=set(), underdetermined=["plot", "facing", "bhk"]),
    dict(id="u03", text="East facing site, need 3BHK with car parking and utility.",
         plot=None, facing="east", beds=3, storeys=None, vastu=None,
         cats={"parking", "utility"}, underdetermined=["plot"]),
    dict(id="u04", text="30x40 site in Pune. How many bedrooms can we fit?",
         plot=(30, 40), facing=None, beds=None, storeys=None, vastu=None,
         cats=set(), underdetermined=["facing", "bhk"]),
    dict(id="u05", text="Design a modern house for us. Budget is 40 lakhs.",
         plot=None, facing=None, beds=None, storeys=None, vastu=None,
         cats=set(), underdetermined=["plot", "facing", "bhk"]),
    dict(id="u06", text="G+1 house, vastu, north facing, joint family. Pooja and "
                        "two car parking a must.",
         plot=None, facing="north", beds=None, storeys=2, vastu=True,
         cats={"pooja", "parking"}, underdetermined=["plot", "bhk"]),
]

DETERMINED = [p for p in PROMPTS if not p.get("underdetermined")]
UNDERDETERMINED = [p for p in PROMPTS if p.get("underdetermined")]


# ==========================================================================
# OFFLINE: spec.py
# ==========================================================================

def test_default_spec_is_structurally_valid():
    s = default_indian_spec()
    assert s.validate() == []
    assert s.bhk_label == "2BHK"


def test_spec_json_roundtrip_is_lossless():
    s = default_indian_spec()
    assert DesignSpec.from_dict(s.to_dict()).to_dict() == s.to_dict()


def test_from_dict_tolerates_junk_and_nulls():
    s = DesignSpec.from_dict({
        "plot_width_ft": None, "city_profile": None, "storeys": None,
        "wild_key": 1,
        "rooms": [{"id": "a", "category": "bedroom", "bogus": 2}, None],
        "adjacency": [{"a": "a", "b": "kitchen", "kind": "required", "zzz": 1}],
        "entrance": None, "vastu": {"enabled": True, "strictness": "strict"},
    })
    assert s.city_profile == "generic_in" and s.storeys == 1
    assert [r.id for r in s.rooms] == ["a"]
    assert s.vastu.enabled and s.rooms[0].min_sqft == 100


def test_room_defaults_fill_from_category():
    r = RoomSpec(id="p", category="pooja")
    assert (r.min_sqft, r.max_sqft, r.max_aspect) == (9, 45, 1.6)
    assert r.name == "Pooja"


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: setattr(s, "plot_width_ft", None), "plot dimensions unknown"),
    (lambda s: setattr(s, "plot_width_ft", 4), "below 10 ft"),
    (lambda s: setattr(s, "city_profile", "atlantis"), "unknown city_profile"),
    (lambda s: setattr(s, "storeys", 7), "outside supported range"),
    (lambda s: setattr(s, "wet_grouping", "maybe"), "wet_grouping"),
    (lambda s: setattr(s, "north_deg", 400.0), "north_deg"),
    (lambda s: s.rooms.append(RoomSpec(id="x", category="jacuzzi")),
     "unknown category"),
    (lambda s: s.rooms.append(RoomSpec(id="living", category="living")),
     "duplicate room id"),
    (lambda s: s.rooms.append(RoomSpec(id="z", category="bedroom", min_sqft=200,
                                       max_sqft=100)), "max_sqft"),
    (lambda s: s.rooms.append(RoomSpec(id="z", category="bedroom",
                                       min_aspect=0.5)), "min_aspect"),
    (lambda s: s.rooms.append(RoomSpec(id="z", category="bedroom", priority=9)),
     "priority"),
    (lambda s: s.rooms.append(RoomSpec(id="z", category="bedroom",
                                       preferred_zone="up")), "preferred_zone"),
    (lambda s: s.rooms.append(RoomSpec(id="z", category="kitchen",
                                       attached_bath=True)), "attached_bath"),
    (lambda s: s.adjacency.append(Adjacency("living", "living")), "to itself"),
    (lambda s: s.adjacency.append(Adjacency("living", "narnia")),
     "unknown room/category"),
    (lambda s: s.adjacency.append(Adjacency("living", "kitchen", "prohibited",
                                            "direct_access")),
     "both required and prohibited"),
    (lambda s: setattr(s.entrance, "side", "up"), "entrance.side"),
    (lambda s: setattr(s.entrance, "side", "west"), "not the road-facing side"),
    (lambda s: setattr(s.vastu, "strictness", "very"), "vastu.strictness"),
    (lambda s: setattr(s.vastu, "enabled", True), "strictness is 'none'"),
    (lambda s: setattr(s, "storeys", 2), "no staircase"),
])
def test_validate_catches_contradiction(mutate, needle):
    s = default_indian_spec()
    # the 2-storey case needs the adjacency list intact but no stair
    mutate(s)
    errs = s.validate()
    assert any(needle in e for e in errs), f"{needle!r} not in {errs}"


def test_validate_catches_impossible_programme():
    s = DesignSpec(plot_width_ft=20, plot_depth_ft=30, road_facing_side="north",
                   rooms=bhk_programme(4, pooja=True, utility=True),
                   entrance=EntranceSpec(side="north"))
    assert any("cannot fit" in e for e in s.validate())


def test_validate_passes_a_realistic_tight_case():
    """A 3BHK on the modal 30x40 G+1 must NOT be rejected as impossible."""
    s = DesignSpec(plot_width_ft=30, plot_depth_ft=40, road_facing_side="east",
                   city_profile="bengaluru", storeys=2,
                   rooms=bhk_programme(3, pooja=True, utility=True,
                                       parking=True, storeys=2),
                   entrance=EntranceSpec(side="east"))
    assert s.validate() == []


def test_advisories_are_not_errors():
    s = DesignSpec(plot_width_ft=30, plot_depth_ft=40, road_facing_side="east",
                   rooms=[RoomSpec(id="b1", category="bedroom"),
                          RoomSpec(id="b2", category="bedroom"),
                          RoomSpec(id="b3", category="bedroom")],
                   entrance=EntranceSpec(side="east"))
    assert s.validate() == []
    adv = " ".join(s.advisories())
    assert "no kitchen" in adv and "no living" in adv and "no bathroom" in adv


def test_apartment_unit_needs_no_plot():
    """An apartment has no plot; faking one would invalidate every site rule."""
    s = DesignSpec(site_kind="apartment_unit", road_facing_side="east",
                   unit_area=AreaQuote(saleable_sqft=1150, carpet_sqft=785,
                                       rera_carpet_sqft=733,
                                       quoted_as="saleable"),
                   rooms=bhk_programme(2), entrance=EntranceSpec(side="east"))
    assert s.validate() == []
    assert s.plot_area_sqft is None
    assert s.estimated_buildable_sqft() == 785      # carpet, not a fake plot


def test_apartment_unit_rejects_a_fabricated_plot():
    s = DesignSpec(site_kind="apartment_unit", plot_width_ft=30,
                   plot_depth_ft=40,
                   unit_area=AreaQuote(carpet_sqft=800), rooms=bhk_programme(2))
    assert any("must not carry plot dimensions" in e for e in s.validate())


def test_apartment_unit_without_an_area_is_underdetermined():
    s = DesignSpec(site_kind="apartment_unit", rooms=bhk_programme(2))
    assert any("no area quoted" in e for e in s.validate())


@pytest.mark.parametrize("quote,expect", [
    (AreaQuote(carpet_sqft=785), 785),
    (AreaQuote(rera_carpet_sqft=733), 733),
    (AreaQuote(builtup_sqft=880), 800),
    (AreaQuote(super_builtup_sqft=1100, loading_factor=1.4), 785.71),
    (AreaQuote(saleable_sqft=1150), 821.43),          # default 1.40 loading
    (AreaQuote(), None),
])
def test_area_stack_resolves_to_carpet(quote, expect):
    got = quote.resolved_carpet_sqft()
    assert (got is None and expect is None) or abs(got - expect) < 0.02


def test_area_stack_must_be_non_decreasing():
    s = DesignSpec(site_kind="apartment_unit",
                   unit_area=AreaQuote(carpet_sqft=900, builtup_sqft=800),
                   rooms=bhk_programme(2))
    assert any("non-decreasing" in e for e in s.validate())


def test_implausible_loading_factor_is_rejected():
    s = DesignSpec(site_kind="apartment_unit",
                   unit_area=AreaQuote(saleable_sqft=1000, loading_factor=4.0),
                   rooms=bhk_programme(2))
    assert any("loading_factor" in e for e in s.validate())


def test_bhk_label_full_carries_the_market_shorthand():
    s = DesignSpec(rooms=bhk_programme(3, pooja=True), half_bhk=True,
                   plot_width_ft=30, plot_depth_ft=50)
    assert s.bhk_label_full == "3.5 BHK + 3T"
    assert s.bhk_label == "3BHK"


def test_builder_room_categories_exist():
    """Vocabulary measured off real Indian builder plans, absent from ResPlan."""
    for cat in ("powder", "handwash", "shaft", "patio", "servant", "foyer",
                "sit_out", "pooja", "utility", "toilet", "store", "study"):
        assert cat in ROOM_CATEGORIES, cat
    assert ROOM_CATEGORIES["powder"].wet and ROOM_CATEGORIES["handwash"].wet
    assert ROOM_CATEGORIES["patio"].outdoor


def test_half_bhk_without_a_den_is_an_advisory_not_an_error():
    s = DesignSpec(plot_width_ft=30, plot_depth_ft=40, road_facing_side="east",
                   half_bhk=True, rooms=bhk_programme(3),
                   entrance=EntranceSpec(side="east"))
    assert s.validate() == []
    assert any("half_bhk" in a for a in s.advisories())


def test_bengaluru_coverage_matches_measured_bbmp():
    """BBMP on a 1200 sqft plot: front 0.9 m, rear 0.7 m, one side 0.7 m,
    stated coverage 75%. The heuristic must land on that, not on a guess."""
    from fpeval.spec import coverage_for
    assert abs(coverage_for("bengaluru", 1200) - 0.75) < 0.01
    s = DesignSpec(plot_width_ft=30, plot_depth_ft=40, city_profile="bengaluru")
    assert abs(s.estimated_buildable_sqft() - 900) < 1


def test_tight_but_real_programmes_are_advisories_not_errors():
    """A 2BHK on a 20x30 is tight and gets built all the time. The
    impossibility check must not reject it -- only clearly unbuildable asks."""
    s = DesignSpec(plot_width_ft=20, plot_depth_ft=30, road_facing_side="north",
                   city_profile="chennai", rooms=bhk_programme(2),
                   entrance=EntranceSpec(side="north"))
    assert s.validate() == []
    assert any("tight fit" in a for a in s.advisories())


def test_bhk_programme_expands_hall_and_kitchen():
    rooms = bhk_programme(3, pooja=True, storeys=2)
    cats = [r.category for r in rooms]
    assert cats.count("bedroom") + cats.count("master_bedroom") == 3
    assert "living" in cats and "kitchen" in cats and "staircase" in cats
    assert next(r for r in rooms if r.category == "pooja").preferred_zone == "NE"


def test_area_unit_bridge():
    r = RoomSpec(id="a", category="bedroom", min_sqft=100, max_sqft=100)
    assert abs(r.min_area_mm2 / 1e6 - 9.2903) < 0.001   # 100 sqft ~= 9.29 m2


# ==========================================================================
# OFFLINE: schema
# ==========================================================================

def test_schema_shape():
    """Optionality is an explicit null on a required key, not an absent key.

    That is the encoding that measured better (see
    DesignSpec.to_json_schema), so the shape is asserted rather than left to
    drift: every key required, `additionalProperties` closed, and no enum ever
    paired with a type union (which the API rejects outright).
    """
    sch = DesignSpec.to_json_schema()
    assert sch["additionalProperties"] is False

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                req, props = set(node.get("required", [])), set(node["properties"])
                # unit_area is the one exception: plain-typed, nothing required
                assert req in (props, set()), sorted(props - req)
            if "enum" in node:
                assert isinstance(node.get("type"), str), node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(sch)
    # the fields that must be expressible as "the brief does not say"
    for k in ("plot_width_ft", "plot_depth_ft", "road_facing_side"):
        node = sch["properties"][k]
        nullable = ("anyOf" in node and any(b.get("type") == "null"
                                            for b in node["anyOf"])) \
            or (isinstance(node.get("type"), list) and "null" in node["type"])
        assert nullable, k


def test_schema_stays_inside_the_strict_grammar_budget():
    """Measured API caps for strict tool use: 16 unions, 24 optional params.

    The spec schema IS strict, so it has to fit. Exceeding either limit is a
    hard 400, not a warning, and adding one nullable field is enough to do it.
    """
    def counts(node, acc=None):
        acc = acc if acc is not None else {"unions": 0, "optional": 0}
        if isinstance(node, dict):
            if "anyOf" in node or isinstance(node.get("type"), list):
                acc["unions"] += 1
            if node.get("type") == "object":
                acc["optional"] += len(set(node.get("properties", {}))
                                       - set(node.get("required", [])))
            for v in node.values():
                counts(v, acc)
        elif isinstance(node, list):
            for v in node:
                counts(v, acc)
        return acc
    c = counts(DesignSpec.to_json_schema())
    assert c["unions"] <= 16, c
    assert c["optional"] <= 24, c


def test_schema_has_no_coordinate_fields():
    """The governing principle, enforced at the schema level."""
    blob = json.dumps(DesignSpec.to_json_schema()).lower()
    for banned in ('"x"', '"y"', "polygon", "coordinate", "_mm", "wall_id"):
        assert banned not in blob, banned


def test_extract_tool_schema_wraps_the_spec_with_its_questions():
    sch = L.extract_spec_tool_schema()
    assert set(sch["properties"]) == {"spec", "clarifying_questions",
                                      "assumptions", "underdetermined"}
    q = sch["properties"]["clarifying_questions"]["items"]
    assert set(q["properties"]) == {"question", "blocking"}
    payload = {"spec": _schema_shaped(default_indian_spec()),
               "clarifying_questions": [{"question": "q?", "blocking": True}],
               "assumptions": ["a"], "underdetermined": False}
    assert L.validate_against_schema(payload, sch) == []
    spec = DesignSpec.from_dict(payload["spec"])
    assert spec.bhk_label == "2BHK" and spec.validate() == []


def test_extract_tool_schema_is_not_strict_by_default():
    """Strict mode is unreachable for this schema; the docstring explains why.
    The default must therefore be non-strict or every call burns a round-trip.
    """
    import inspect
    sig = inspect.signature(L.extract_spec_tool_schema)
    assert sig.parameters["strict"].default is False
    assert inspect.signature(L.extract_spec).parameters["strict"].default is False


def test_schema_covers_every_spec_field():
    props = set(DesignSpec.to_json_schema()["properties"])
    fields = set(DesignSpec.__dataclass_fields__) - {"spec_version", "provenance"}
    assert fields <= props, fields - props


def _schema_shaped(spec) -> dict:
    """Serialise in exactly the schema's shape.

    Nulls are kept -- that is the encoding. The one exception is `unit_area`,
    which is plain-typed with an empty `required` list to stay inside the
    union budget, so its unquoted figures are omitted rather than nulled.
    """
    d = spec.to_dict()
    d.pop("spec_version", None)
    d.pop("provenance", None)
    d["unit_area"] = {k: v for k, v in d["unit_area"].items() if v is not None}
    return d


def test_generated_spec_validates_against_its_own_schema():
    """A spec we build ourselves must satisfy the schema we hand the model."""
    sch = DesignSpec.to_json_schema()
    assert L.validate_against_schema(_schema_shaped(default_indian_spec()),
                                     sch) == []
    unit = DesignSpec(
        site_kind="apartment_unit",
        unit_area=AreaQuote(saleable_sqft=1150, carpet_sqft=785,
                            rera_carpet_sqft=733, quoted_as="saleable"),
        road_facing_side="east", rooms=bhk_programme(2),
        entrance=EntranceSpec(side="east"))
    assert L.validate_against_schema(_schema_shaped(unit), sch) == []


def test_minimal_schema_validator():
    sch = {"type": "object",
           "properties": {"a": {"type": ["number", "null"]},
                          "b": {"anyOf": [{"type": "string", "enum": ["x"]},
                                          {"type": "null"}]},
                          "c": {"type": "array", "items": {"type": "integer"}}},
           "required": ["a", "b", "c"], "additionalProperties": False}
    assert L.validate_against_schema({"a": None, "b": None, "c": [1]}, sch) == []
    assert L.validate_against_schema({"a": 1, "b": "x", "c": []}, sch) == []
    assert L.validate_against_schema({"a": "s", "b": "x", "c": []}, sch)
    assert L.validate_against_schema({"a": 1, "b": "z", "c": []}, sch)
    assert L.validate_against_schema({"a": 1, "b": "x", "c": [1.5]}, sch)
    assert L.validate_against_schema({"a": 1, "b": "x"}, sch)
    assert L.validate_against_schema({"a": 1, "b": "x", "c": [], "d": 0}, sch)
    # booleans must not satisfy number/integer
    assert L.validate_against_schema({"a": True, "b": "x", "c": []}, sch)


def test_patch_schema_shape():
    sch = L.propose_patch_tool_schema()
    op = sch["properties"]["ops"]["items"]
    assert set(op["properties"]["op"]["enum"]) == set(L.OP_TABLE)
    assert set(op["properties"]["level"]["enum"]) == {
        L.LEVEL_SPEC, L.LEVEL_GEOMETRY, L.LEVEL_FURNITURE}
    assert sch["additionalProperties"] is False
    # Every op's declared params must exist in the flat params object, or the
    # model has no way to express them.
    params = set(op["properties"]["params"]["properties"])
    for name, e in L.OP_TABLE.items():
        missing = (set(e["required"]) | set(e["optional"])) - params
        assert not missing, (name, missing)
    # Same statement, from the library's own guard, so a future op cannot be
    # added to OP_TABLE without a field to carry its arguments.
    assert L.schema_param_gaps() == {}


# ==========================================================================
# OFFLINE: patch protocol
# ==========================================================================

def _op(op, **params):
    return L.PatchOp(op=op, params=params, description=f"do {op}",
                     finding_ids=["f1"], confidence=0.8)


def test_every_op_in_the_table_is_reachable_and_documented():
    for name, e in L.OP_TABLE.items():
        assert e["level"] in (L.LEVEL_SPEC, L.LEVEL_GEOMETRY, L.LEVEL_FURNITURE)
        assert e["doc"]
        # A geometry op names the editor function it mirrors; spec ops change
        # the brief and have no editor counterpart. Furniture ops are mixed:
        # `furnish_room` and `set_kitchen_layout` are ours, the rest map to the
        # editor's furniture calls.
        if e["level"] == L.LEVEL_SPEC:
            assert e["editor"] is None, name
        elif e["level"] == L.LEVEL_GEOMETRY:
            assert e["editor"], name


def test_patchop_serialisation_roundtrip():
    o = _op("set_room_area", room_id="bed1", min_sqft=140, max_sqft=180)
    assert L.PatchOp.from_dict(o.to_dict()).to_dict() == o.to_dict()
    assert o.level == L.LEVEL_SPEC


def test_patchop_level_is_derived_not_trusted():
    o = L.PatchOp(op="update_wall", params={"wall_id": "w1", "thickness_mm": 230},
                  description="thicken", level="spec")
    assert any("level=" in e for e in o.validate())


def test_description_is_mandatory():
    o = L.PatchOp(op="set_wet_grouping", params={"value": "required"})
    assert any("description is required" in e for e in o.validate())


def test_coordinate_emission_is_rejected():
    """The governing principle, enforced at the op level."""
    for bad in ({"x": 100, "y": 200}, {"start": [0, 0]}, {"dx": 10},
                {"polygon": [[0, 0]]}, {"points": []}, {"bbox": [0, 0, 1, 1]}):
        o = L.PatchOp(op="move_wall_parallel", params=bad, description="nudge",
                      finding_ids=["f1"])
        errs = o.validate()
        assert any("absolute" in e or "unexpected param" in e for e in errs), bad
    nested = L.PatchOp(op="add_wall",
                       params={"start_ref": {"x": 1, "y": 2}, "end_ref": "w1:end"},
                       description="add", finding_ids=["f1"])
    assert any("point literal" in e or "symbolic reference" in e
               for e in nested.validate())


def test_unknown_op_is_rejected():
    assert any("unknown op" in e for e in
               L.PatchOp(op="teleport_room", description="d").validate())


def test_unexpected_param_is_rejected():
    o = _op("remove_room", room_id="bed1", min_sqft=10)
    assert any("unexpected param 'min_sqft'" in e for e in o.validate())


def test_missing_required_param_is_rejected():
    assert any("missing required param 'room_id'" in e
               for e in _op("set_room_zone", preferred_zone="NE").validate())


@pytest.mark.parametrize("op,params,needle", [
    ("set_room_area", dict(room_id="bed1", min_sqft=200, max_sqft=100), "max_sqft"),
    ("set_room_aspect", dict(room_id="bed1", max_aspect=0.4), "min_aspect"),
    ("set_room_zone", dict(room_id="bed1", preferred_zone="up"), "preferred_zone"),
    ("add_room", dict(room_id="gym", category="gym"), "unknown category"),
    ("set_adjacency", dict(a="kitchen", b="living", kind="maybe"), "kind"),
    ("set_entrance", dict(side="up"), "side"),
    ("set_wet_grouping", dict(value="sometimes"), "value"),
    ("set_storeys", dict(value=9), "1..4"),
    ("move_wall_parallel", dict(wall_id="w1", direction="up", distance_mm=100),
     "direction"),
    ("move_wall_parallel", dict(wall_id="w1", direction="north", distance_mm=90000),
     "outside 10..5000"),
    ("split_wall", dict(wall_id="w1", at=1.5), "within"),
    ("split_wall", dict(wall_id="w1", at="somewhere"), "midpoint"),
    ("add_wall", dict(start_ref="w1", end_ref="w2:end"), "symbolic reference"),
    ("add_wall", dict(start_ref="w1:end", end_ref="w1:end"), "identical"),
    ("add_door", dict(wall_id="w1", position=3.0), "position"),
    ("add_door", dict(wall_id="w1", position="centre", door_type="portal"),
     "door_type"),
    ("update_wall", dict(wall_id="w1", thickness_mm=9000), "thickness_mm"),
    ("update_wall", dict(wall_id="w1", height_mm=100), "height_mm"),
    ("update_door", dict(door_id="o1", width_mm=10), "width_mm"),
    ("update_door", dict(door_id="o1", swing_direction="up"), "swing_direction"),
    ("update_room", dict(room_id="r1", room_type="bedroom"), "room_type"),
])
def test_per_op_semantics(op, params, needle):
    errs = _op(op, **params).validate()
    assert any(needle in e for e in errs), f"{needle!r} not in {errs}"


def test_geometry_nudge_must_cite_a_finding():
    o = L.PatchOp(op="move_wall_parallel",
                  params={"wall_id": "w1", "direction": "north",
                          "distance_mm": 150},
                  description="shift the wall north by 150 mm")
    assert any("cite the finding" in e for e in o.validate())
    o.finding_ids = ["f7"]
    assert o.validate() == []


def test_spec_referential_integrity():
    s = default_indian_spec()
    assert any("no room 'nope'" in e for e in
               _op("set_room_priority", room_id="nope", priority=1).validate(spec=s))
    assert any("already exists" in e for e in
               _op("add_room", room_id="kitchen", category="kitchen").validate(spec=s))
    assert _op("set_room_priority", room_id="kitchen", priority=1).validate(spec=s) == []


def test_plan_referential_integrity():
    from fpeval.ir import Plan, Wall, P
    plan = Plan(id="t", walls=[Wall(id="w1", start=P(0, 0), end=P(1000, 0),
                                    thickness=230)])
    good = _op("update_wall", wall_id="w1", thickness_mm=230)
    bad = _op("update_wall", wall_id="w9", thickness_mm=230)
    assert good.validate(plan=plan) == []
    assert any("no wall 'w9'" in e for e in bad.validate(plan=plan))


def test_parse_patch_ops_partitions_good_from_bad():
    s = default_indian_spec()
    raw = [
        _op("set_room_area", room_id="bed1", min_sqft=140, max_sqft=185).to_dict(),
        _op("set_room_zone", room_id="pooja", preferred_zone="NE").to_dict(),
        _op("set_room_zone", room_id="ghost", preferred_zone="NE").to_dict(),
        {"op": "nonsense", "description": "x"},
        _op("move_wall_parallel", wall_id="w1", direction="north",
            distance_mm=150).to_dict(),
    ]
    good, bad = L.parse_patch_ops(raw, spec=s)
    assert [o.op for o in good] == ["set_room_area", "set_room_zone",
                                    "move_wall_parallel"]
    assert len(bad) == 2 and all(errs for _, errs in bad)


def test_batch_separates_levels_and_flags_resolve():
    batch = L.PatchBatch(
        ops=[_op("set_room_area", room_id="bed1", min_sqft=140, max_sqft=180),
             _op("add_door", wall_id="w1", position="centre")],
        rejected=[({"op": "nope"}, ["unknown op"])],
        description="Fix bedroom width")
    assert batch.requires_resolve
    assert len(batch.spec_ops) == 1 and len(batch.geometry_ops) == 1
    diff = "\n".join(batch.diff_lines())
    assert "[SPEC]" in diff and "[GEOM]" in diff and "REJECTED" in diff
    assert "re-run" in diff


def test_editor_script_wraps_one_undo_group():
    batch = L.PatchBatch(
        ops=[_op("update_wall", wall_id="w1", thickness_mm=230),
             _op("add_door", wall_id="w1", position="centre", door_type="single"),
             _op("move_wall_parallel", wall_id="w2", direction="east",
                 distance_mm=150),
             _op("remove_element", element_id="o3")],
        description="Widen the corridor")
    js = batch.to_editor_script()
    assert js.startswith("beginUndoGroup();")
    assert js.rstrip().endswith('endUndoGroup("Widen the corridor");')
    # mm -> cm, matching OpenPlan3D's float-centimetre Project
    assert 'updateWall("w1", {"thickness": 23.0});' in js
    assert 'addDoor("w1", 0.5, "single");' in js
    # east at north_deg=0 is +x, no +y component
    assert 'moveWallParallel("w2", 15.0, 0.0);' in js
    assert 'removeElement("o3");' in js
    assert js.count("beginUndoGroup") == 1


def test_editor_script_is_empty_for_pure_spec_batches():
    b = L.PatchBatch(ops=[_op("set_storeys", value=2)], description="x")
    assert b.to_editor_script() == ""


def test_move_direction_respects_north_deg():
    b = L.PatchBatch(ops=[_op("move_wall_parallel", wall_id="w1",
                              direction="north", distance_mm=1000)])
    assert 'moveWallParallel("w1", 0.0, 100.0);' in b.to_editor_script(north_deg=0)
    # rotate the site 90 deg: north now lies along -x in plan space
    assert 'moveWallParallel("w1", -100.0, 0.0);' in b.to_editor_script(north_deg=90)


def test_apply_to_spec_mutates_only_the_spec_level():
    s = default_indian_spec()
    before = s.to_dict()
    batch = L.PatchBatch(ops=[
        _op("set_room_area", room_id="bed1", min_sqft=150, max_sqft=190),
        _op("set_room_zone", room_id="pooja", preferred_zone="NE"),
        _op("add_room", room_id="utility", category="utility"),
        _op("remove_room", room_id="sitout"),
        _op("set_adjacency", a="utility", b="kitchen", kind="required",
            relation="adjacent"),
        _op("set_wet_grouping", value="required"),
        _op("set_entrance", zone="NE"),
        _op("update_wall", wall_id="w1", thickness_mm=230),   # ignored here
    ], description="Rework")
    out = batch.apply_to_spec(s)
    assert s.to_dict() == before                      # no mutation in place
    assert out.room("bed1").min_sqft == 150
    assert out.room("utility") is not None and out.room("sitout") is None
    assert out.wet_grouping == "required" and out.entrance.zone == "NE"
    assert any(a.a == "utility" for a in out.adjacency)
    assert out.provenance["patches"][0]["description"] == "Rework"
    assert out.validate() == []


def test_remove_room_also_drops_its_adjacencies():
    s = default_indian_spec()
    s.adjacency.append(Adjacency("bed1", "kitchen", "prohibited", "adjacent"))
    out = L.PatchBatch(ops=[_op("remove_room", room_id="bed1")]).apply_to_spec(s)
    assert all("bed1" not in (a.a, a.b) for a in out.adjacency)


def test_coerce_op_params_drops_nulls_and_restores_numbers():
    d = L._coerce_op_params({"op": "add_door",
                             "params": {"wall_id": "w1", "position": "0.35",
                                        "room_id": None, "at": "midpoint",
                                        "distance_mm": "150"}})
    assert d["params"] == {"wall_id": "w1", "position": 0.35, "at": "midpoint",
                           "distance_mm": 150.0}


def test_summarize_plan_is_symbolic():
    from fpeval.ir import Plan, Wall, Room, Opening, P
    plan = Plan(id="t",
                walls=[Wall(id="w1", start=P(0, 0), end=P(3000, 0), thickness=230)],
                openings=[Opening(id="o1", kind="door", wall_id="w1",
                                  position=0.5, width=900)],
                rooms=[Room(id="r1", name="Hall", category="living",
                            wall_ids=["w1"], polygon=[], area=12_000_000)])
    txt = L.summarize_plan(plan)
    assert "w1" in txt and "o1" in txt and "r1" in txt
    assert "129 sqft" in txt          # 12 m2
    assert "length=3000mm" in txt


def test_render_findings_accepts_dataclass_and_dict():
    txt = L._render_findings([
        L.Finding(id="f1", severity="error", code="NBC.MIN_WIDTH",
                  message="too narrow", refs=["r2"], suggestion="widen"),
        {"id": "f2", "severity": "warn", "code": "VASTU.POOJA", "message": "sw"},
    ])
    assert "[f1] ERROR NBC.MIN_WIDTH" in txt and "validator hint: widen" in txt
    assert "[f2] WARN" in txt


def test_null_validator_satisfies_the_protocol():
    assert isinstance(L.NullValidator(), L.Validator)
    assert L.NullValidator().check(None, default_indian_spec()) == []


def test_usage_log_costs_and_summarises():
    u = L.UsageLog()
    u.add(L.CallRecord(op="a", model="claude-opus-5", input_tokens=1_000_000,
                       output_tokens=0))
    u.add(L.CallRecord(op="b", model="claude-sonnet-5", input_tokens=0,
                       output_tokens=1_000_000, attempts=3, ok=False))
    s = u.summary()
    assert s["calls"] == 2 and s["retries"] == 2 and s["failures"] == 1
    assert abs(s["total_cost_usd"] - 15.0) < 1e-6
    assert set(s["by_model"]) == {"claude-opus-5", "claude-sonnet-5"}


# ==========================================================================
# OFFLINE: brief.py
# ==========================================================================

@pytest.mark.parametrize("text,flagged", [
    ("three bedrooms and 2 bathrooms", False),
    ("we need 3 bedrooms", False),
    ("about 120 sqft", True),
    ("120 sq ft living", True),
    ("a 30x40 plot", True),
    ("30 by 40 site", True),
    ("12 x 14 master bedroom", True),
    ("3.6 m wide", True),
    ("2400 mm door", True),
    ("area of 1200", True),
    ("1200 square feet", True),
    ("proportions matter more than exact sizes", False),
    ("one bedroom clearly the largest", False),
])
def test_absolute_dimension_guard(text, flagged):
    assert B.has_absolute_dimension_claim(text) is flagged


def test_synthetic_briefs_are_nondegenerate():
    briefs = B.synthetic_indian_briefs(200, seed=1)
    assert len(briefs) == 200
    assert len(set(briefs)) > 150                 # not a template loop
    assert all(30 < len(b) < 900 for b in briefs), \
        (min(map(len, briefs)), max(map(len, briefs)))
    assert all(b == b.strip() and b.endswith((".", "!")) for b in briefs)
    joined = " ".join(briefs).lower()
    for token in ("bhk", "facing", "vastu", "pooja", "parking", "lakh"):
        assert token in joined, token
    # no template placeholders leaked
    assert "{" not in joined and " none " not in joined


def test_synthetic_distribution_matches_the_market_model():
    r = B.synthetic_distribution_report(3000, seed=0)
    assert 0.10 < r["underdetermined_rate"] < 0.20
    assert 0.38 < r["vastu_rate"] < 0.52
    beds = r["bedrooms"]
    assert max(beds, key=beds.get) == 3           # 3BHK is the modal Indian ask
    assert set(r["register"]) == {"terse", "plain", "verbose", "apartment"}
    assert set(r["site_kind"]) == {"plot", "apartment_unit"}
    assert 0.03 < r["half_bhk_rate"] < 0.15
    assert r["facing"]["east"] > r["facing"]["south"]


def test_synthetic_set_contains_apartment_briefs_with_no_plot():
    recs = B.synthetic_indian_brief_records(400, seed=8)
    units = [r for r in recs if r.site_kind == "apartment_unit"]
    assert 40 < len(units) < 130, len(units)      # ~20% of the set
    for r in units:
        assert r.plot_width_ft is None and r.plot_depth_ft is None
        assert r.storeys == 1
        assert "bhk" in r.brief.lower() or "unit" in r.brief.lower()
        if "area" not in r.underdetermined:
            assert r.carpet_sqft and r.saleable_sqft
            assert 1.15 < r.saleable_sqft / r.carpet_sqft < 1.85


def test_synthetic_set_carries_builder_vocabulary():
    joined = " ".join(B.synthetic_indian_briefs(500, seed=9))
    for token in ("TYPE", "PDR", "SR", "ST", "carpet", "RERA", "saleable",
                  "SITOUT", "UTILITY", "PUJA", "STUDY", "BALCONY", ".5 BHK",
                  " T"):
        assert token in joined, token


def test_synthetic_records_carry_scoreable_ground_truth():
    recs = B.synthetic_indian_brief_records(60, seed=2)
    for r in recs:
        if r.site_kind == "apartment_unit":
            continue
        assert (r.plot_width_ft is None) == ("plot" in r.underdetermined)
        assert (r.road_facing_side is None) == ("facing" in r.underdetermined)
        if r.plot_width_ft:
            assert f"{r.plot_width_ft}x{r.plot_depth_ft}" in r.brief
        if r.road_facing_side and r.register != "terse":
            assert r.road_facing_side in r.brief
    assert any(r.underdetermined for r in recs)


def test_synthetic_briefs_are_deterministic():
    assert B.synthetic_indian_briefs(20, seed=5) == \
        B.synthetic_indian_briefs(20, seed=5)
    assert B.synthetic_indian_briefs(20, seed=5) != \
        B.synthetic_indian_briefs(20, seed=6)


def test_synthetic_plausibility_no_absurd_programmes():
    """The sampler must not ask for a 4BHK on a 600 sqft single-floor plot."""
    for r in B.synthetic_indian_brief_records(1500, seed=4):
        if r.plot_width_ft is None or r.site_kind == "apartment_unit":
            continue
        buildable = r.plot_width_ft * r.plot_depth_ft * 0.6 * r.storeys
        assert buildable / r.bedrooms > 110, r


# -- ResPlan-backed brief tests (need the corpus, not the API) -------------

def _resplan_available() -> bool:
    try:
        B.resplan_path()
        return True
    except FileNotFoundError:
        return False


requires_corpus = pytest.mark.skipif(not _resplan_available(),
                                     reason="ResPlan.pkl not on disk")


@requires_corpus
def test_plan_to_brief_on_20_real_plans():
    plans = B.resplan_plans(20, seed=7)
    assert len(plans) == 20
    briefs = [B.plan_to_brief(p) for p in plans]

    for p, b in zip(plans, briefs):
        # non-degenerate
        assert 120 < len(b) < 1600, (p.id, len(b))
        assert b.count(".") >= 4
        assert "{" not in b and "None" not in b
        # NO absolute dimension claims -- ResPlan cannot support one
        assert not B.has_absolute_dimension_claim(b), \
            (p.id, B.absolute_dimension_hits(b))
        # the room counts in the brief must match the plan
        facts = B.brief_facts(p)
        if facts["n_bedrooms"] >= 2:
            from fpeval.brief import _NUMWORD
            assert _NUMWORD[facts["n_bedrooms"]] in b
    assert len(set(briefs)) >= 15          # variation, not one template


@requires_corpus
def test_plan_to_brief_is_deterministic_per_plan():
    p = B.resplan_plans(1, seed=9)[0]
    assert B.plan_to_brief(p) == B.plan_to_brief(p)


@requires_corpus
def test_plan_to_brief_keeps_provenance_honest():
    p = B.resplan_plans(1, seed=11)[0]
    rec = B.plan_to_brief_record(p)
    prov = rec["provenance"]
    assert prov["source"] == "ResPlan"
    assert "unstated" in prov["geography"] and "away from India" in prov["geography"]
    assert "NOT relabelled as Indian" in prov["vocabulary"]
    assert prov["absolute_dimensions_asserted"] is False
    footer = B.plan_to_brief(p, include_provenance=True)
    assert "NOT an Indian brief" in footer and "+/-30%" in footer


@requires_corpus
def test_plan_to_brief_uses_no_indian_vocabulary():
    """DECISIONS: do not relabel a non-Indian corpus as Indian."""
    joined = " ".join(B.plan_to_brief(p) for p in B.resplan_plans(30, seed=13))
    for token in ("bhk", "pooja", "puja", "hall", "vastu", "lakh", "sit-out",
                  "utility", "mandir", "crore"):
        assert token not in joined.lower(), token


# ==========================================================================
# API: extraction accuracy
# ==========================================================================

def _cats(spec) -> set[str]:
    return {r.category for r in spec.rooms}


def score_extraction(spec, questions, truth) -> dict:
    """Score one extraction against hand-labelled expectations."""
    und = truth.get("underdetermined") or []
    got_plot = (spec.plot_width_ft, spec.plot_depth_ft)
    want_plot = truth["plot"]

    out: dict = {"id": truth["id"], "failures": []}

    want_kind = truth.get("site_kind", "plot")
    out["site_kind"] = spec.site_kind == want_kind
    if not out["site_kind"]:
        out["failures"].append(f"site_kind {spec.site_kind!r} != {want_kind!r}")

    if truth.get("no_plot_expected"):
        # An apartment has no plot. Fabricating one would silently invalidate
        # every setback / coverage / FAR check downstream.
        ok = got_plot == (None, None)
        out["plot"] = ok
        if not ok:
            out["failures"].append(
                f"fabricated a plot {got_plot} for an apartment unit")
        area = spec.unit_area.resolved_carpet_sqft()
        out["unit_area"] = area is not None
        if area is None:
            out["failures"].append("apartment unit with no resolvable carpet area")
    elif "plot" in und:
        ok = got_plot == (None, None)
        out["plot"] = ok
        if not ok:
            out["failures"].append(f"invented plot {got_plot} (none was given)")
    elif want_plot is None:
        out["plot"] = None
    else:
        # orientation may legitimately be read either way round for a square
        ok = got_plot == want_plot or got_plot == tuple(reversed(want_plot))
        out["plot"] = ok
        if not ok:
            out["failures"].append(f"plot {got_plot} != {want_plot}")

    if "facing" in und or truth["facing"] is None:
        ok = spec.road_facing_side is None if "facing" in und else None
        out["facing"] = ok
        if ok is False:
            out["failures"].append(
                f"invented facing {spec.road_facing_side!r}")
    else:
        ok = spec.road_facing_side == truth["facing"]
        out["facing"] = ok
        if not ok:
            out["failures"].append(
                f"facing {spec.road_facing_side!r} != {truth['facing']!r}")

    if truth["beds"] is None:
        out["beds"] = None
    else:
        ok = spec.bedroom_count == truth["beds"]
        out["beds"] = ok
        if not ok:
            out["failures"].append(
                f"{spec.bedroom_count} bedrooms != {truth['beds']}")

    if truth["storeys"] is None:
        out["storeys"] = None
    else:
        ok = spec.storeys == truth["storeys"]
        out["storeys"] = ok
        if not ok:
            out["failures"].append(f"storeys {spec.storeys} != {truth['storeys']}")

    if truth["vastu"] is None:
        out["vastu"] = None
    else:
        ok = spec.vastu.enabled == truth["vastu"]
        out["vastu"] = ok
        if not ok:
            out["failures"].append(f"vastu {spec.vastu.enabled} != {truth['vastu']}")

    missing = set(truth["cats"]) - _cats(spec)
    out["cats"] = not missing
    if missing:
        out["failures"].append(f"missing categories {sorted(missing)}")

    prov = spec.provenance or {}
    out["asked"] = bool(questions)
    out["asked_blocking"] = bool(prov.get("blocking_questions"))
    out["n_questions"] = len(questions)
    out["expected_questions"] = bool(und)
    out["questions_ok"] = out["asked_blocking"] if und else True
    if und and not out["asked_blocking"]:
        out["failures"].append("underdetermined but asked no blocking question")

    out["questions"] = list(questions)
    out["structural_errors"] = spec.validate()
    out["contract_violations"] = (spec.provenance or {}).get(
        "contract_violations", [])
    return out


EVAL_MODEL = os.environ.get("FPEVAL_LLM_MODEL", L.MODEL_REASONING)


def run_extraction_eval(model: str = EVAL_MODEL, workers: int = 8,
                        prompts=None) -> dict:
    """Run the whole prompt suite and return honest aggregate numbers."""
    prompts = prompts or PROMPTS
    usage = L.UsageLog()
    client = L.get_client()

    def one(t):
        try:
            spec, qs = L.extract_spec(t["text"], model=model, client=client,
                                      usage=usage)
        except Exception as exc:                      # schema failure counts
            return {"id": t["id"], "hard_failure": f"{type(exc).__name__}: {exc}",
                    "failures": ["hard failure"], "questions_ok": False,
                    "structural_errors": ["extraction raised"],
                    "contract_violations": []}
        return score_extraction(spec, qs, t)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, prompts))

    def rate(key):
        vals = [r.get(key) for r in rows if r.get(key) is not None
                and "hard_failure" not in r]
        return (sum(bool(v) for v in vals), len(vals))

    hard = [r for r in rows if "hard_failure" in r]
    determined = [r for r in rows if not r.get("expected_questions")
                  and "hard_failure" not in r]
    under = [r for r in rows if r.get("expected_questions")]

    return {
        "model": model,
        "n": len(rows),
        "schema_conformance": (len(rows) - len(hard), len(rows)),
        "hard_failures": [(r["id"], r["hard_failure"]) for r in hard],
        "plot": rate("plot"), "facing": rate("facing"), "beds": rate("beds"),
        "storeys": rate("storeys"), "vastu": rate("vastu"), "cats": rate("cats"),
        "site_kind": rate("site_kind"), "unit_area": rate("unit_area"),
        "all_fields_correct": (sum(1 for r in rows
                                   if not r["failures"] and "hard_failure" not in r),
                               len(rows)),
        "underdetermined_asked_blocking": (
            sum(1 for r in under if r.get("asked_blocking")), len(under)),
        "determined_any_question": (
            sum(1 for r in determined if r.get("asked")), len(determined)),
        "determined_blocking_question": (
            sum(1 for r in determined if r.get("asked_blocking")), len(determined)),
        "mean_questions_determined": round(
            sum(r.get("n_questions", 0) for r in determined)
            / max(len(determined), 1), 2),
        # An underdetermined prompt SHOULD fail validate() -- a missing plot
        # size is a blocking error by design. Measuring it over all rows would
        # score correct behaviour as a defect, so it is split.
        "structurally_valid_determined": (
            sum(1 for r in determined if not r["structural_errors"]),
            len(determined)),
        "underdetermined_correctly_blocked": (
            sum(1 for r in under if r["structural_errors"]), len(under)),
        "contract_violations": sum(len(r["contract_violations"]) for r in rows),
        "rows": rows,
        "usage": usage.summary(),
    }


@requires_api
def test_extract_spec_on_the_prompt_suite(capsys):
    res = run_extraction_eval()
    with capsys.disabled():
        print("\n" + format_extraction_report(res))
    ok, n = res["schema_conformance"]
    # Schema conformance is the contract and must be perfect: the reply is
    # validated locally and a failure is fed back for repair, so anything less
    # is a real failure rather than a flaky model.
    assert ok == n, res["hard_failures"]

    by_id = {p["id"]: p for p in PROMPTS}
    for r in res["rows"]:
        und = by_id[r["id"]].get("underdetermined") or []
        # The behaviour that actually matters: a missing plot size must halt
        # the pipeline with a question, never be filled in with a guess.
        if "plot" in und:
            assert r["plot"] is True, f"{r['id']} invented a plot size"
            assert r["asked_blocking"], f"{r['id']} did not block on a missing plot"
        # An apartment unit must never acquire a plot either.
        if by_id[r["id"]].get("no_plot_expected"):
            assert r["plot"] is True, f"{r['id']} fabricated a plot for a unit"
        # Every underdetermined prompt must at least ask something.
        if und:
            assert r["asked"], f"{r['id']} asked nothing about {und}"


def format_extraction_report(res: dict) -> str:
    def pct(t):
        got, tot = t
        return f"{got}/{tot} = {100.0 * got / max(tot, 1):.1f}%"
    lines = [
        f"=== extract_spec eval ({res['model']}, n={res['n']}) ===",
        f"  schema conformance     {pct(res['schema_conformance'])}",
        f"  struct. valid (deter.) {pct(res['structurally_valid_determined'])}",
        f"  underdet. blocked      {pct(res['underdetermined_correctly_blocked'])}",
        f"  plot dimensions        {pct(res['plot'])}",
        f"  road facing            {pct(res['facing'])}",
        f"  bedroom count          {pct(res['beds'])}",
        f"  storeys                {pct(res['storeys'])}",
        f"  vastu flag             {pct(res['vastu'])}",
        f"  required categories    {pct(res['cats'])}",
        f"  site_kind plot/unit    {pct(res['site_kind'])}",
        f"  unit area resolvable   {pct(res['unit_area'])}",
        f"  every field correct    {pct(res['all_fields_correct'])}",
        f"  underdet. asked block. {pct(res['underdetermined_asked_blocking'])}",
        f"  determ. asked anything {pct(res['determined_any_question'])}",
        f"  determ. asked BLOCKING {pct(res['determined_blocking_question'])}"
        f"   (false halts)",
        f"  mean questions/determ. {res['mean_questions_determined']}",
        f"  contract violations    {res['contract_violations']}",
        "  failures:",
    ]
    for r in res["rows"]:
        if r["failures"]:
            lines.append(f"    {r['id']}: " + "; ".join(r["failures"]))
        if r["structural_errors"] and not r.get("expected_questions"):
            lines.append(f"    {r['id']} [struct]: "
                         + "; ".join(r["structural_errors"][:2]))
    lines.append("  usage: " + json.dumps(res["usage"]))
    return "\n".join(lines)


# ==========================================================================
# API: round-trip probe (brief -> extract_spec -> compare to source plan)
# ==========================================================================

def run_roundtrip_probe(n: int = 20, model: str = L.MODEL_BULK,
                        paraphrase: int = 0, workers: int = 8) -> dict:
    """brief -> extract_spec -> recovered programme vs the source plan's rooms.

    Reported honestly, including the fact that a templated brief makes this
    easy: the brief literally names the counts, so anything below ~100% is a
    parsing defect, not a design-understanding result. `paraphrase>0` rewrites
    that many briefs with sonnet first, which is the harder and more meaningful
    number.
    """
    plans = B.resplan_plans(n, seed=17)
    recs = [B.plan_to_brief_record(p) for p in plans]
    briefs = [r["brief"] for r in recs]
    usage = L.UsageLog()
    client = L.get_client()

    if paraphrase:
        briefs[:paraphrase] = B.paraphrase_briefs(
            briefs[:paraphrase], client=client, usage=usage)

    def one(i):
        try:
            spec, qs = L.extract_spec(briefs[i], model=model, client=client,
                                      usage=usage)
        except Exception as exc:
            return {"plan": recs[i]["facts"]["plan_id"],
                    "hard_failure": f"{type(exc).__name__}: {exc}"}
        f = recs[i]["facts"]
        got_bed = spec.bedroom_count
        got_bath = len([r for r in spec.rooms
                        if r.category in ("bathroom", "toilet")])
        return {
            "plan": f["plan_id"],
            "paraphrased": i < paraphrase,
            "bed_true": f["n_bedrooms"], "bed_got": got_bed,
            "bath_true": f["n_bathrooms"], "bath_got": got_bath,
            "balcony_true": f["n_balconies"],
            "balcony_got": len([r for r in spec.rooms if r.category == "balcony"]),
            "store_true": f["n_storage"],
            "store_got": len([r for r in spec.rooms
                              if r.category in ("store", "storage")]),
            "asked": bool(qs),
            "invented_plot": spec.plot_width_ft is not None,
            "spurious_indian": sorted(
                {r.category for r in spec.rooms}
                & {"pooja", "sit_out", "parking", "utility"}),
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, range(len(briefs))))

    good = [r for r in rows if "hard_failure" not in r]

    def exact(key):
        return (sum(1 for r in good if r[f"{key}_true"] == r[f"{key}_got"]),
                len(good))

    def confusion(key):
        c: dict[str, int] = {}
        for r in good:
            k = f"{r[f'{key}_true']}->{r[f'{key}_got']}"
            c[k] = c.get(k, 0) + 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1]))

    return {
        "model": model, "n": len(rows), "paraphrased": paraphrase,
        "hard_failures": [r for r in rows if "hard_failure" in r],
        "bedrooms_exact": exact("bed"), "bedrooms_confusion": confusion("bed"),
        "bathrooms_exact": exact("bath"), "bathrooms_confusion": confusion("bath"),
        "balconies_exact": exact("balcony"),
        "storage_exact": exact("store"),
        "asked_a_question": sum(1 for r in good if r["asked"]),
        "invented_a_plot_size": sum(1 for r in good if r["invented_plot"]),
        "leaked_indian_categories": sum(1 for r in good if r["spurious_indian"]),
        "rows": rows,
        "usage": usage.summary(),
    }


@requires_api
@requires_corpus
def test_roundtrip_probe(capsys):
    res = run_roundtrip_probe(20, paraphrase=6)
    with capsys.disabled():
        print("\n" + format_roundtrip_report(res))
    assert not res["hard_failures"], res["hard_failures"]
    got, tot = res["bedrooms_exact"]
    # Reported, not tuned. The threshold is loose on purpose: this probe exists
    # to expose the confusion, not to be passed.
    assert got / tot >= 0.7, res["bedrooms_confusion"]
    # A ResPlan brief states no plot size, so the extractor must not invent one.
    assert res["invented_a_plot_size"] == 0


def format_roundtrip_report(res: dict) -> str:
    def pct(t):
        got, tot = t
        return f"{got}/{tot} = {100.0 * got / max(tot, 1):.1f}%"
    return "\n".join([
        f"=== round-trip probe ({res['model']}, n={res['n']}, "
        f"{res['paraphrased']} paraphrased) ===",
        f"  bedrooms exact    {pct(res['bedrooms_exact'])}  "
        f"confusion {res['bedrooms_confusion']}",
        f"  bathrooms exact   {pct(res['bathrooms_exact'])}  "
        f"confusion {res['bathrooms_confusion']}",
        f"  balconies exact   {pct(res['balconies_exact'])}",
        f"  storage exact     {pct(res['storage_exact'])}",
        f"  asked a question  {res['asked_a_question']}/{res['n']}",
        f"  invented a plot   {res['invented_a_plot_size']}/{res['n']}",
        f"  leaked IN cats    {res['leaked_indian_categories']}/{res['n']}",
        "  usage: " + json.dumps(res["usage"]),
    ])


# ==========================================================================
# API: patch proposal
# ==========================================================================

def _demo_plan():
    from fpeval.ir import Plan, Wall, Room, Opening, Site, P
    W = [Wall(id=f"w{i}", start=P(*s), end=P(*e), thickness=230)
         for i, (s, e) in enumerate([
             ((0, 0), (9144, 0)), ((9144, 0), (9144, 12192)),
             ((9144, 12192), (0, 12192)), ((0, 12192), (0, 0)),
             ((0, 5000), (9144, 5000)), ((4000, 5000), (4000, 12192)),
             ((0, 2000), (4000, 2000)),
         ])]
    rooms = [
        Room(id="r_living", name="Hall", category="living",
             wall_ids=["w0", "w3", "w4"], polygon=[], area=14_900_000),
        Room(id="r_kitchen", name="Kitchen", category="kitchen",
             wall_ids=["w4", "w6"], polygon=[], area=6_000_000),
        Room(id="r_bed1", name="Master Bedroom", category="master_bedroom",
             wall_ids=["w4", "w5", "w2"], polygon=[], area=11_000_000),
        Room(id="r_pooja", name="Pooja Room", category="pooja",
             wall_ids=["w5", "w2"], polygon=[], area=1_200_000),
    ]
    op = [Opening(id="o0", kind="front_door", wall_id="w0", position=0.5, width=1000)]
    return Plan(id="demo", walls=W, openings=op, rooms=rooms,
                site=Site(plot_polygon=[P(0, 0), P(9144, 0), P(9144, 12192),
                                        P(0, 12192)], north_deg=0.0))


DEMO_FINDINGS = [
    L.Finding(id="f1", severity="error", code="NBC.ROOM_MIN_WIDTH",
              message="Kitchen clear width is 1.9 m, below the 2.1 m minimum.",
              refs=["r_kitchen"], suggestion="widen the kitchen"),
    L.Finding(id="f2", severity="error", code="VASTU.POOJA_ZONE",
              message="Pooja room sits in the south-west; a strict-vastu brief "
                      "requires the north-east.", refs=["r_pooja"]),
    L.Finding(id="f3", severity="error", code="PLAN.NO_DOOR",
              message="Master bedroom has no door on any of its walls.",
              refs=["r_bed1", "w5"]),
    L.Finding(id="f4", severity="warn", code="PROG.AREA_SHORT",
              message="Master bedroom is 118 sqft against a 130-250 sqft target.",
              refs=["r_bed1"]),
    L.Finding(id="f5", severity="warn", code="VASTU.NO_TOILET_NE",
              message="No common toilet in the programme at all.", refs=[]),
]


@requires_api
def test_propose_patch_emits_valid_symbolic_ops(capsys):
    # Room ids match _demo_plan() so the referential checks actually bite.
    spec = DesignSpec(
        plot_width_ft=30, plot_depth_ft=40, road_facing_side="east",
        city_profile="bengaluru", storeys=1,
        rooms=[
            RoomSpec(id="r_living", category="living", name="Hall", priority=1),
            RoomSpec(id="r_kitchen", category="kitchen", priority=1,
                     preferred_zone="SE"),
            RoomSpec(id="r_bed1", category="master_bedroom", priority=1,
                     attached_bath=True),
            RoomSpec(id="r_bath1", category="bathroom", priority=1),
            RoomSpec(id="r_pooja", category="pooja", priority=2,
                     preferred_zone="SW"),      # wrong on purpose -- finding f2
        ],
        entrance=EntranceSpec(side="east"),
        vastu=VastuSpec(enabled=True, strictness="strict",
                        requirements=["pooja_northeast", "kitchen_southeast"]))

    plan = _demo_plan()
    usage = L.UsageLog()
    batch = L.propose_patch_batch(plan, DEMO_FINDINGS, spec, plan=plan,
                                  usage=usage)
    with capsys.disabled():
        print("\n=== propose_patch ===")
        print("\n".join(batch.diff_lines()))
        print("notes:", batch.notes)
        print("editor script:\n" + (batch.to_editor_script() or "(none)"))
        print("usage:", json.dumps(usage.summary()))

    assert batch.ops, "no valid ops proposed"
    # every kept op re-validates
    for o in batch.ops:
        assert o.validate(spec=spec, plan=plan) == []
        assert o.description.strip()
    # spec-level must dominate -- that is the stated preference
    assert len(batch.spec_ops) >= len(batch.geometry_ops)
    # applying the spec ops must leave a structurally valid spec
    out = batch.apply_to_spec(spec)
    assert out.validate() == [], out.validate()
    # finding f2 says the pooja is in the south-west under a strict-vastu
    # brief, so the patch must move it north-east (or drop it and say why).
    pooja = out.room("r_pooja")
    assert pooja is None or pooja.preferred_zone in ("NE", "N", "E"), \
        pooja.preferred_zone
    assert any("f2" in o.finding_ids for o in batch.ops), \
        "the vastu finding was not addressed at all"
    # and no coordinates anywhere in the payload
    blob = json.dumps(batch.to_dict())
    assert '"x"' not in blob and '"polygon"' not in blob


# ==========================================================================
# Manual driver: prints the full report
# ==========================================================================

if __name__ == "__main__":
    if not API:
        print("set FPEVAL_LLM_API=1 to run the API evals")
        raise SystemExit(1)
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "extract"):
        model = sys.argv[2] if len(sys.argv) > 2 else L.MODEL_REASONING
        print(format_extraction_report(run_extraction_eval(model=model)))
    if which in ("all", "roundtrip"):
        print(format_roundtrip_report(run_roundtrip_probe(20, paraphrase=6)))
