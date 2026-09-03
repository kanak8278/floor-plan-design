"""Design principles: the general half goes in the prompt, the specific half is a validator.

The split matters. A principle stated only in a prompt is unenforced -- the model
may ignore it and nothing notices. A check with no stated principle is a rule the
model cannot anticipate, so it discovers it by failing. Pairing them means:

  * every principle names the checks that enforce it, and
  * every check is reachable from a principle a human can read.

`audit()` reports either half without the other, so the two cannot silently drift
the way my earlier modules did when they were written and never called.

The principles are deliberately QUALITATIVE. Numbers belong in `standards.py`
and `bylaws.py`, where they carry a clause reference; restating "2400 mm" in
prose gives two sources of truth for one number.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principle:
    id: str
    text: str                       # goes verbatim into the system prompt
    enforced_by: tuple[str, ...]    # validator rule ids
    scopes: tuple[str, ...] = ()    # scenario keys; empty = all
    why: str = ""


PRINCIPLES: tuple[Principle, ...] = (
    # ---- organisation ---------------------------------------------------
    Principle(
        "P.PUBLIC_CORE",
        "Organise the plan around a public core. The hall or living space is the "
        "most connected room; every other room hangs off it or off a passage "
        "leading to it. Never let one room be the only route to another.",
        ("SYNTAX.LIVING_NOT_CORE", "SYNTAX.WEAK_HIERARCHY",
         "DESIGN.BEDROOM_THROUGH_TRAFFIC"),
        why="Measured on 400 real plans the public core carries 2.53x the plan's "
            "average integration; a minimum-spanning-tree door graph produced 1.01x."),
    Principle(
        "P.PRIVACY_GRADIENT",
        "Bedrooms are leaves, not corridors. A bedroom has one door to "
        "circulation, plus at most one more to its own bathroom. Private rooms "
        "should sit deeper in the plan than public ones.",
        ("SYNTAX.PRIVATE_ROOM_INTEGRATED", "SYNTAX.NO_PRIVACY_GRADIENT",
         "DESIGN.BEDROOM_THROUGH_TRAFFIC", "DESIGN.MULTIPLE_ATTACHED_BATHS",
         "DESIGN.BEDROOM_OFF_LIVING", "DESIGN.TOO_DEEP")),
    Principle(
        "P.ZONING",
        "Group public, private and service rooms into contiguous zones. Do not "
        "interleave a bedroom between two service rooms, or scatter bedrooms "
        "across opposite ends of the plan.",
        ("ZONE.PRIVATE_FRAGMENTED", "ZONE.PUBLIC_FRAGMENTED",
         "DESIGN.BEDROOMS_SCATTERED", "DESIGN.DEAD_END_CIRCULATION")),
    Principle(
        "P.ARRIVAL",
        "Arrival is a sequence: sitout or porch, then a foyer, then the hall. "
        "The front door must never open into a bedroom or a kitchen, and "
        "preferably not straight into the seating area.",
        ("DESIGN.ENTRANCE_INTO_PRIVATE", "DESIGN.ENTRANCE_NO_BUFFER",
         "TYPO.ENTRY_SEQUENCE")),

    # ---- service relationships ------------------------------------------
    Principle(
        "P.SERVING_DISTANCE",
        "Kitchen, dining and hall form the service chain. The kitchen opens to "
        "the dining area; the dining area is visible from or open to the hall. "
        "Put the utility directly off the kitchen and the dry store within reach.",
        ("TYPO.MISSING_ADJACENCY", "DESIGN.STORE_FAR_FROM_KITCHEN",
         "DESIGN.KITCHEN_SINGLE_ACCESS")),
    Principle(
        "P.WET_GROUPING",
        "Stack and group wet rooms. Kitchen, bathrooms and utility should share "
        "walls or a plumbing line, and on a multi-storey plan sit above one "
        "another.",
        ("DESIGN.KITCHEN_FAR_FROM_PARKING",)),
    Principle(
        "P.ONE_KITCHEN",
        "A single dwelling has one kitchen. Multiple kitchens are correct only "
        "when the building is genuinely several units, one per floor.",
        ("DESIGN.MULTIPLE_KITCHENS",)),

    # ---- bathrooms -------------------------------------------------------
    Principle(
        "P.BATH_ACCESS",
        "Bathroom access must match what was asked for. An attached bath opens "
        "off one bedroom only. A shared bath opens off exactly two. A common "
        "bath opens off circulation so a guest never enters a bedroom. Provide "
        "at least one bath reachable without passing through a bedroom.",
        ("TOPO.ATTACHED_BATH_SHORTFALL", "TOPO.BATH_OVERSHARED",
         "TOPO.NO_COMMON_BATH", "TOPO.BATH_UNREACHABLE",
         "DESIGN.SOLE_BATH_VIA_BEDROOM")),
    Principle(
        "P.NO_WC_ONTO_FOOD",
        "A toilet must never open into a kitchen, and must not open onto or be "
        "visible from the dining area.",
        ("NBC.WC_OPENS_INTO_KITCHEN", "DESIGN.DINING_ABUTS_WC",
         "DESIGN.WC_VISIBLE_FROM_DINING")),

    # ---- habitability ----------------------------------------------------
    Principle(
        "P.LIGHT_AND_AIR",
        "Every habitable room needs an external wall and a window. Prefer light "
        "from two sides, and put the inlet and outlet of a cross-draught on "
        "different faces of the building.",
        ("DESIGN.NO_WINDOW", "DESIGN.SINGLE_ASPECT",
         "NBC.VENTILATION_HABITABLE", "NBC.VENTILATION_KITCHEN",
         "NBC.VENTILATION_BATHROOM")),
    Principle(
        "P.USABLE_WALLS",
        "A room needs usable wall, not just floor. Leave a clear run for a "
        "wardrobe in each bedroom and a counter run in the kitchen; do not fill "
        "every wall with doors and windows.",
        ("DESIGN.NO_WARDROBE_WALL",)),
    Principle(
        "P.STORAGE",
        "Provide real storage: a store room or a utility, not only wardrobes.",
        ("DESIGN.NO_STORAGE",)),

    # ---- vertical --------------------------------------------------------
    Principle(
        "P.STAIR_FROM_HALL",
        "A staircase rises from the hall or a landing, never from inside a "
        "bedroom, and its flight must be comfortable to climb.",
        ("NBC.STAIR_RISER", "NBC.STAIR_TREAD", "NBC.STAIR_COMFORT",
         "NBC.STAIR_WIDTH", "NBC.STAIR_NO_LANDING"),
        scopes=("duplex", "rental_floors")),

    # ---- site ------------------------------------------------------------
    Principle(
        "P.SITE_FIT",
        "Respect the site: stay inside the setbacks, under the ground-coverage "
        "cap and under the FAR. Park within the compound and keep the walk from "
        "the car to the kitchen short.",
        ("BYLAW.SETBACK_ENCROACH", "BYLAW.GROUND_COVERAGE", "BYLAW.FAR",
         "DESIGN.KITCHEN_FAR_FROM_PARKING"),
        scopes=("house_compact", "house_standard", "house_mid", "villa",
                "duplex", "rental_floors")),

    # ---- optional, config-gated -----------------------------------------
    Principle(
        "P.VASTU",
        "When Vastu is requested, honour the client's stated directions first "
        "and the conventional zones second: kitchen south-east, master bedroom "
        "south-west, pooja north-east, entrance north or east. Keep the centre "
        "of the plan light. Never silently override a direction the client gave.",
        ("VASTU.KITCHEN", "VASTU.MASTER_BEDROOM", "VASTU.TOILET",
         "VASTU.ENTRANCE", "VASTU.BRAHMASTHAN"),
        why="Config-gated: only stated when policy.vastu.mode != 'off'."),
)


def for_scenario(scenario_key: str, *, vastu: bool = True) -> list[Principle]:
    out = []
    for p in PRINCIPLES:
        if p.id == "P.VASTU" and not vastu:
            continue
        if p.scopes and scenario_key not in p.scopes:
            continue
        out.append(p)
    return out


def prompt_block(scenario_key: str = "house_standard", *, vastu: bool = True,
                 numbered: bool = True) -> str:
    """The general half, as prompt text.

    Qualitative on purpose. Dimensions live in `standards.py` with a clause
    reference; repeating them here would give one number two homes.
    """
    ps = for_scenario(scenario_key, vastu=vastu)
    lines = ["Design principles. These are how the plan is judged; the exact "
             "dimensional limits are checked separately and reported back to you.",
             ""]
    for i, p in enumerate(ps, 1):
        head = f"{i}. " if numbered else "- "
        lines.append(f"{head}{p.text}")
    lines += ["", "A violation of any of these comes back as a finding naming the "
                  "rule and the rooms involved. Fix the cause, not the symptom."]
    return "\n".join(lines)


# Families exempt from needing a principle. GEO is arithmetic -- "rooms must not
# overlap" is not design advice, it is a validity condition. The declarative
# BYLAW checks report an UNDECLARED brief field, which is paperwork, not design.
EXEMPT_PREFIXES = ("GEO.", "TYPO.ASSUMED", "BYLAW.SITE_UNSPECIFIED",
                   "BYLAW.UNIT_NOT_A_SITE", "BYLAW.MAX_FLOORS_UNCHECKED",
                   "BYLAW.RWH_UNDECLARED", "BYLAW.ROAD_WIDTH_UNDECLARED")


def audit(known_rule_ids: set[str]) -> dict[str, list[str]]:
    """Principles citing unknown checks, and checks no principle mentions.

    Exists because a principle without a check is unenforced and a check without
    a principle is a rule the model cannot anticipate.
    """
    cited: set[str] = set()
    dangling: list[str] = []
    for p in PRINCIPLES:
        for rid in p.enforced_by:
            cited.add(rid)
            if rid not in known_rule_ids:
                dangling.append(f"{p.id} -> {rid}")
    orphans = sorted(r for r in (known_rule_ids - cited)
                     if not r.startswith(EXEMPT_PREFIXES))
    return {"principles_citing_unknown_checks": sorted(dangling),
            "checks_with_no_principle": orphans}
