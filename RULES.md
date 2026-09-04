# Every rule, one line each

The flat checklist. `SPACES.md` explains *why* each rule exists and what each
space is for; this file is just the list, for reading straight through.

**Status:** `ok` implemented and live in `validate()` · `part` implemented but
blind to a case named in the line · `GAP` not implemented.

**Totals: 175 rules — 100 implemented, 75 gaps.** Verified against source, not
recalled — `VASTU.POOJA_ZONE` and `NBC.ROOM_MIN_WIDTH` appear in `llm.py`
docstrings only and are not real rule ids.

**Layout.** Families 1-100 are live in `validate()`. Families 101-175 are not,
split into three: the ordinary gaps, which are waiting on work; `DORMANT`, 22
rules nothing in the current output can trip and why; and `REVIEW`, the 12 that
came out of reading the 89 plans in `out/suite_svg`.

Rules 164-175 came from the review recorded in `FLOORPLAN_ISSUES.md`. Seven
further proposals from the same review were rejected and are listed with reasons
in `REVIEW`, along with the amendments that review forced on #25, #51, #60, #67,
#77, #101, #129 and #163. Numbering is stable: every id from 1 to 175 appears
exactly once, and moving a rule to `DORMANT` does not renumber it.

---

## GEO — validity arithmetic (12 ok)

Not design advice. "Rooms must not overlap" is a condition for the document
being readable at all, which is why this family is exempt from needing a
principle behind it.

| # | ID | Sev | Rule |
|---|---|---|---|
| 1 | `GEO.NO_ROOMS` | error | ok — the plan contains no rooms |
| 2 | `GEO.NO_FRONT_DOOR` | error | ok — no opening is marked as the front door |
| 3 | `GEO.ROOM_OVERLAP` | error | ok — two room polygons overlap by more than a shared-edge sliver |
| 4 | `GEO.ROOM_DEGENERATE` | error | ok — a room polygon has zero or near-zero area |
| 5 | `GEO.ROOM_OUTSIDE_ENVELOPE` | error | ok — a room falls outside the buildable envelope |
| 6 | `GEO.ROOM_AREA_MISMATCH` | warn | ok — a room's stated area disagrees with its polygon |
| 7 | `GEO.UNREACHABLE_ROOM` | error | ok — a room has no door path from the entrance |
| 8 | `GEO.OPENING_ORPHAN` | error | ok — an opening names a wall that does not exist |
| 9 | `GEO.OPENING_OFF_WALL` | warn | ok — an opening's position falls outside 0..1 on its wall |
| 10 | `GEO.OPENING_OVERRUNS_WALL` | warn | ok — an opening extends past the end of its host wall |
| 11 | `GEO.OPENING_TOO_WIDE` | error | ok — an opening is wider than the wall can carry |
| 12 | `GEO.WALL_TOO_SHORT` | warn | ok — a wall under ~100 mm, usually a snap artefact |

## NBC — law (19: 18 ok, 1 part)

National Building Code of India 2016, Parts 3 and 4. Every number carries a
clause reference in `bylaws.py` / `standards.py`; none is restated here.

| # | ID | Sev | Rule |
|---|---|---|---|
| 13 | `NBC.HAB_MIN_AREA` | error | ok — a habitable room is under the 7.5 m² floor |
| 14 | `NBC.HAB_MIN_WIDTH` | error | ok — a habitable room is under 2400 mm clear width |
| 15 | `NBC.HAB_CEILING` | error | ok — a habitable room is under 2750 mm floor-to-ceiling |
| 16 | `NBC.KITCHEN_MIN_AREA` | error | ok — a kitchen is under the 5.0 m² floor |
| 17 | `NBC.KITCHEN_MIN_WIDTH` | error | ok — a kitchen is under 1800 mm clear width |
| 18 | `NBC.KITCHEN_CEILING` | error | ok — a kitchen is under 2750 mm floor-to-ceiling |
| 19 | `NBC.BATH_MIN_AREA` | error | ok — a bath+WC is under the 2.8 m² floor |
| 20 | `NBC.BATH_MIN_WIDTH` | error | ok — a bathroom is under 1200 mm clear width |
| 21 | `NBC.BATH_CEILING` | error | ok — a bathroom is under 2100 mm floor-to-ceiling |
| 22 | `NBC.PASSAGE_WIDTH` | error | ok — a passage is under 900 mm clear between its door thresholds |
| 23 | `NBC.WC_OPENS_INTO_KITCHEN` | error | ok — a WC door opens directly into a kitchen |
| 24 | `NBC.VENTILATION_HABITABLE` | error | ok — glazing under 1 m² per 10 m² of habitable floor, no mechanical relief |
| 25 | `NBC.VENTILATION_KITCHEN` | warn | part — kitchen glazing under the minimum; an exhaust can substitute, but a utility with its own exterior opening does not yet count as a substitute (review: it should) |
| 26 | `NBC.VENTILATION_BATHROOM` | warn | ok — bathroom glazing under the minimum; an exhaust can substitute |
| 27 | `NBC.STAIR_RISER` | error | ok — a riser over 190 mm |
| 28 | `NBC.STAIR_TREAD` | error | ok — a tread under 250 mm |
| 29 | `NBC.STAIR_WIDTH` | error | ok — a flight under 900 mm wide |
| 30 | `NBC.STAIR_COMFORT` | warn | ok — the flight fails the 2R+T comfort band |
| 31 | `NBC.STAIR_NO_LANDING` | error | ok — more consecutive risers than the code allows without a landing |

## BYLAW — the site, and what was never declared (11 ok)

Bengaluru / BBMP bands first. The `UNDECLARED` and `UNSPECIFIED` variants are
paperwork, not design: they say a check could not run, which is not the same as
saying it passed.

| # | ID | Sev | Rule |
|---|---|---|---|
| 32 | `BYLAW.SETBACK_ENCROACH` | error | ok — built form crosses a setback line |
| 33 | `BYLAW.GROUND_COVERAGE` | error | ok — footprint exceeds the coverage cap for the plot band |
| 34 | `BYLAW.FAR` | error | ok — total built area exceeds the permitted FAR |
| 35 | `BYLAW.MAX_FLOORS` | error | ok — more floors than the band and road width allow |
| 36 | `BYLAW.ROAD_WIDTH` | error | ok — the abutting road is narrower than the band requires |
| 37 | `BYLAW.RWH_REQUIRED` | error | ok — rainwater harvesting is mandatory at this plot size and absent |
| 38 | `BYLAW.SITE_UNSPECIFIED` | warn | ok — no surveyed plot, so setback/coverage/FAR were not checked at all |
| 39 | `BYLAW.UNIT_NOT_A_SITE` | warn | ok — an apartment unit was given plot rules that do not apply to it |
| 40 | `BYLAW.MAX_FLOORS_UNCHECKED` | warn | ok — the brief never stated storeys, so the floor cap is unverified |
| 41 | `BYLAW.ROAD_WIDTH_UNDECLARED` | warn | ok — the brief never stated road width, so the band is a guess |
| 42 | `BYLAW.RWH_UNDECLARED` | warn | ok — the brief never said whether RWH is provided |

## DESIGN — the architect's red pen (27: 23 ok, 4 part)

A plan can pass every dimensional rule and be unusable. Each of these is
individually switchable via `policy.RuleConfig`, because they are judgements.

| # | ID | Sev | Rule |
|---|---|---|---|
| 43 | `DESIGN.BEDROOM_THROUGH_TRAFFIC` | error | ok — a private room is the only route to another room (bath and balcony exempt) |
| 44 | `DESIGN.BEDROOM_OFF_LIVING` | warn | ok — a bedroom door opens off the living room in a plan big enough to have afforded a passage instead |
| 45 | `DESIGN.BEDROOMS_SCATTERED` | warn | part — bedrooms spread across the plan; measures straight-line spread, not walking distance |
| 46 | `DESIGN.SOLE_BATH_VIA_BEDROOM` | error | ok — the plan's only bathroom is reachable only through a bedroom |
| 47 | `DESIGN.MULTIPLE_ATTACHED_BATHS` | error | ok — one bedroom carries more than one en-suite |
| 48 | `DESIGN.DEAD_END_CIRCULATION` | warn | ok — a circulation space with one door leads nowhere |
| 49 | `DESIGN.TOO_DEEP` | warn | ok — a room is more doors from the front door than its typology allows |
| 50 | `DESIGN.ENTRANCE_INTO_PRIVATE` | error | ok — the front door opens into a bedroom or a kitchen |
| 51 | `DESIGN.ENTRANCE_NO_BUFFER` | warn | part — no arrival buffer. Fires today whenever the door lands in a living; review says that is normal and it must fire only when the door lands in *circulation* (see #164) |
| 52 | `DESIGN.DINING_ABUTS_WC` | error | ok — the dining area shares a wall or door with a toilet |
| 53 | `DESIGN.WC_VISIBLE_FROM_DINING` | warn | ok — a toilet door opens into the same room as the dining table, so it is in view while eating |
| 54 | `DESIGN.MULTIPLE_KITCHENS` | error | ok — more than one kitchen in what is meant to be one dwelling |
| 55 | `DESIGN.KITCHEN_SINGLE_ACCESS` | warn | ok — the kitchen has one door, so serving and service traffic share it and there is no back route |
| 56 | `DESIGN.KITCHEN_FAR_FROM_PARKING` | warn | part — groceries carried over 12 m; measured straight-line, so it ignores walls |
| 57 | `DESIGN.STORE_FAR_FROM_KITCHEN` | warn | ok — the dry store shares neither a wall nor a door with the kitchen |
| 58 | `DESIGN.NO_STORAGE` | warn | ok — no store room and no utility; wardrobes are not storage |
| 59 | `DESIGN.NO_WARDROBE_WALL` | warn | ok — a bedroom has no clear 1800 mm wall run for a wardrobe |
| 60 | `DESIGN.NO_WINDOW` | error/warn | part — a habitable room has no window at all. One severity today; review says error for sleeping and living rooms, warn for a study or family room |
| 61 | `DESIGN.SINGLE_ASPECT` | warn | ok — every window in a room sits on one external face, so it gets no cross-light and no cross-ventilation |
| 62 | `DESIGN.BATH_UNDERSIZED` | warn | ok — a bathroom under 2.4 m², the p5 of 3,573 real ones |
| 63 | `DESIGN.BATH_OVERSIZED` | warn | ok — a bathroom over 7.5 m², the p95, which is area taken from the bedrooms |
| 64 | `DESIGN.BATH_DISPROPORTIONATE` | warn | ok — a bath outside 15-40% of the bedroom it serves |
| 65 | `DESIGN.WET_AREA_EXCESSIVE` | warn | ok — all bathrooms together exceed 13% of carpet area |
| 66 | `DESIGN.BALCONY_NO_ACCESS` | error | ok — a balcony with no door into it |
| 67 | `DESIGN.BALCONY_ENCLOSED` | error | ok — a balcony with no open edge; an interior balcony is not a balcony. The test is the open *edge*, never the presence of a window: a balcony is open by definition |
| 68 | `DESIGN.BALCONY_ODD_HOST` | warn | ok — a balcony hosted by a bathroom, store or other room that would not use one |
| 69 | `DESIGN.BALCONY_THROUGH_ROUTE` | warn | ok — a balcony used as the route between two rooms |

## TYPO — expectations that depend on the building (4: 3 ok, 1 part)

A kitchen opening into the living is right in an 1,100 sqft apartment and wrong
in a 4,000 sqft villa. Driven by `typology.TYPOLOGIES`.

| # | ID | Sev | Rule |
|---|---|---|---|
| 70 | `TYPO.MISSING_ADJACENCY` | error/warn | ok — a relation the typology expects (`direct`/`open`/`near`) is absent |
| 71 | `TYPO.FORBIDDEN_ADJACENCY` | error/warn | ok — a relation the typology forbids (`separate`) exists as a door |
| 72 | `TYPO.ENTRY_SEQUENCE` | warn | part — arrival stages out of order by door-depth; takes the *minimum* depth per category, so a second `foyer`-labelled passage deep in the plan is invisible |
| 73 | `TYPO.ASSUMED` | warn | ok — states which typology was used and whether it was inferred rather than given |

## TOPO — bathroom access as a topology (4 ok)

"3BHK with all attached baths" and "3BHK with a common bath" are different
door-graph signatures, not different labels. Kinds: `attached`,
`shared_attached`, `common`, `common_attached`, `detached`, `powder`,
`unreachable`.

| # | ID | Sev | Rule |
|---|---|---|---|
| 74 | `TOPO.BATH_UNREACHABLE` | error | ok — a bathroom with no door |
| 75 | `TOPO.BATH_OVERSHARED` | error | ok — a bath opening off three or more bedrooms; a shared bath serves two |
| 76 | `TOPO.ATTACHED_BATH_SHORTFALL` | error | ok — fewer attached baths than the brief asked for |
| 77 | `TOPO.NO_COMMON_BATH` | warn | ok — every bath is en-suite, so a guest must enter a bedroom. Stays `warn` on review: a preference, not a defect. The hard case is #46, the plan's *only* bath behind a bedroom |

## ZONE — public / private / service read as zones (2 ok)

| # | ID | Sev | Rule |
|---|---|---|---|
| 78 | `ZONE.PRIVATE_FRAGMENTED` | warn | ok — the private rooms form more than one touching cluster |
| 79 | `ZONE.PUBLIC_FRAGMENTED` | warn | ok — the public rooms form more than one touching cluster |

## SYNTAX — space-syntax configuration (4 ok)

Integration from Hillier & Hanson via arXiv 2602.22507, thresholds measured on
real ResPlan plans. Says nothing at all on a disconnected door graph, because
depth does not cross components.

| # | ID | Sev | Rule |
|---|---|---|---|
| 80 | `SYNTAX.LIVING_NOT_CORE` | error | ok — the most integrated space is not a public one; the house is organised around the wrong room |
| 81 | `SYNTAX.WEAK_HIERARCHY` | warn | ok — living-room integration under 1.10x the plan average, against 2.53x in real plans |
| 82 | `SYNTAX.NO_PRIVACY_GRADIENT` | warn | ok — private rooms over 0.85x as integrated as public ones; one undifferentiated zone |
| 83 | `SYNTAX.PRIVATE_ROOM_INTEGRATED` | warn | ok — a private room's integration exceeds the measured p99 for its door count, so it is being used as circulation |

## VASTU — client preference, config-gated (9 ok)

Weighted, not pass/fail, and the client's own stated direction always outranks
the table. Gated off entirely when `brief["vastu"]` is false.

| # | ID | Sev | Rule |
|---|---|---|---|
| 84 | `VASTU.ENTRANCE` | warn | ok — main entrance not in N/NE/E; SW is the strongest taboo |
| 85 | `VASTU.KITCHEN` | warn | ok — kitchen not in the SE Agni corner; NE and SW held adverse |
| 86 | `VASTU.MASTER_BEDROOM` | warn | ok — master bedroom not in the SW; NE discouraged |
| 87 | `VASTU.POOJA` | warn | ok — pooja not in the NE Ishanya corner; S and SW avoided |
| 88 | `VASTU.TOILET` | warn | ok — toilet not in NW/W; NE and SE avoided |
| 89 | `VASTU.STAIRS` | warn | ok — stair not in SW/NW, or sitting over the Brahmasthan |
| 90 | `VASTU.LIVING` | warn | ok — living room not towards N/NE/E (weak preference) |
| 91 | `VASTU.BEDROOM_OTHER` | warn | ok — secondary bedrooms not in S/W/SW/NW (weak preference) |
| 92 | `VASTU.BRAHMASTHAN` | warn | ok — over 35% of the plan's central ninth is built on, where Vastu wants open space |

## BRIEF — did we build what was asked for (8 ok)

Contract compliance, not design judgement, which is why this family needs no
principle behind it. Fed by `bridge.brief_from_spec(spec)`.

| # | ID | Sev | Rule |
|---|---|---|---|
| 93 | `BRIEF.ROOM_MISSING` | error | ok — fewer of a room type than the brief asked for (subtypes and stair/parking resolve) |
| 94 | `BRIEF.ADJACENCY_UNMET` | error | ok — a requested `direct_access` has no door between the rooms |
| 95 | `BRIEF.NOT_TOUCHING` | error | ok — a requested `adjacent` shares neither wall nor door |
| 96 | `BRIEF.FORBIDDEN_ADJACENCY` | error | ok — a prohibited pair has a door between them |
| 97 | `BRIEF.ZONE_UNMET` | warn | ok — a specifically requested Vastu zone was not honoured |
| 98 | `BRIEF.ITEM_MISSING` | warn | ok — a `must_place` furniture item was never placed |
| 99 | `BRIEF.ITEM_MISPLACED` | warn | ok — a placed item is in the wrong room type |
| 100 | `BRIEF.RELATION_UNCHECKED` | warn | ok — a stated `visual` or `same_floor` relation has no test yet; neither confirmed nor denied |

---

# NOT IMPLEMENTED (75)

## PURPOSE — is a room earning its area from where it sits (12 GAP)

The missing *kind* of rule. Every check above asks "does a required relation
exist?". These ask "does this room's position let it do its job?" — and the
answer is usually *delete or relocate it*, not *add a door*. This is the family
an LLM misses entirely, because the room is present and correctly labelled and
nothing looks wrong locally.

| # | ID | Rule |
|---|---|---|
| 101 | `PURPOSE.FOYER_NOT_AT_ENTRY` | GAP, **error** — a foyer deeper than 1 door from the front door is a passage, not a foyer (partly caught today by #48/#70/#72, all as `warn`). Review: must never happen; 53 of the 89 suite plans do it |
| 102 | `PURPOSE.FOYER_UNJUSTIFIED` | GAP — a plan too small for a lobby has one anyway, spending 1.8-8 m² of contested carpet on arrival |
| 103 | `PURPOSE.UTILITY_NOT_OFF_KITCHEN` | GAP — a utility not adjacent to the kitchen means the laundry crosses the house |
| 104 | `PURPOSE.STORE_NOT_OFF_KITCHEN` | GAP — a dry store far from the kitchen is a general store, mislabelled |
| 106 | `PURPOSE.SITOUT_NOT_AT_ARRIVAL` | GAP — a sitout the front door does not open onto, or from which the gate cannot be seen |
| 107 | `PURPOSE.SITOUT_TOO_SHALLOW` | GAP — a sitout under 1800 mm deep is not occupiable |
| 109 | `PURPOSE.PASSAGE_SERVES_ONE` | GAP — a corridor serving fewer than two rooms is wasted carpet |
| 110 | `PURPOSE.POWDER_TOO_DEEP` | GAP — a guest WC deeper than the nearest bedroom's bath; the guest will use that instead |
| 111 | `PURPOSE.FAMILY_LIVING_NOT_SEPARATED` | GAP — a second living sits on the route from the front door, so it duplicates the first instead of giving the family a private one |
| 112 | `PURPOSE.BALCONY_TOO_SHALLOW` | GAP — a balcony under 1200 mm deep is a ledge |
| 118 | `PURPOSE.GATE_OFF_ROAD` | GAP — a gate on a boundary that faces no road |
| 120 | `PURPOSE.DRY_BALCONY_NO_SUN` | GAP — a drying balcony facing north, so washing never gets direct sun |

## REACH — walking distance along circulation (9 GAP)

The second missing family. Everything today is a door-graph hop count or a
straight-line `poly.distance()` that measures through walls. All ten need one
new primitive: a walkable graph of door thresholds and room centroids with
edges weighted by in-plan path length.

| # | ID | Rule |
|---|---|---|
| 121 | `REACH.WC_FROM_LIVING` | GAP — walk from the living seating to the nearest guest-usable WC — *"the living room doesn't have a washroom"* |
| 122 | `REACH.PUBLIC_ZONE_SPAN` | GAP — living-to-dining walk against the plan diagonal — *"hall and living at two ends of the house"* |
| 124 | `REACH.DINING_FROM_KITCHEN` | GAP — serving distance measured in metres, not door hops |
| 125 | `REACH.BATH_FROM_BEDROOM` | GAP — every bedroom without an en-suite, to its nearest bath |
| 126 | `REACH.BEDROOM_CLUSTER_SPAN` | GAP — bedroom-to-bedroom walk, the metric version of #45 |
| 127 | `REACH.UTILITY_FROM_KITCHEN` | GAP — the laundry walk |
| 128 | `REACH.MASTER_FROM_ENTRY` | GAP — the master's walk from the entrance: too short and it is not private, too long and it is a trek |
| 129 | `REACH.CIRCULATION_SHARE` | GAP, **error** — circulation area over ~20% of carpet means the plan is corridor-heavy. Review confirms the threshold and the severity; 10 of the 89 suite plans exceed it, one at 28% |
| 130 | `REACH.TORTUOSITY` | GAP — walking path over straight-line distance above ~1.5 for a key pair, meaning walls force a detour |

## EGRESS — NBC 2016 Part 4, life safety (4 GAP)

Family absent outright. Nothing in the codebase mentions travel distance,
dead-end length or exit width. The two rules needing a stair or a second storey
are in `DORMANT`; these four are testable on a single floor plan today.

| # | ID | Rule |
|---|---|---|
| 131 | `EGRESS.TRAVEL_DISTANCE` | GAP — furthest habitable point to a final exit beyond the 15-45 m limit for the occupancy |
| 132 | `EGRESS.DEAD_END` | GAP — dead-end corridor over 3 m unsprinklered, 6 m sprinklered |
| 133 | `EGRESS.EXIT_DOOR_WIDTH` | GAP — designated exit doorway under 1000 mm, or not opening in the escape direction |
| 136 | `EGRESS.NO_LOCKED_ROUTE` | GAP — the escape route passes through a private room |

## ACCESS — Harmonised Guidelines 2021, universal accessibility (6 GAP)

Family absent outright. Should be gated on a brief flag, since it raises minima
everywhere and must not fire on a plan nobody asked to be accessible.

| # | ID | Rule |
|---|---|---|
| 137 | `ACCESS.DOOR_CLEAR_WIDTH` | GAP — under 900 mm clear (800 mm absolute floor) at the entrance and one bathroom |
| 138 | `ACCESS.TURNING_CIRCLE` | GAP — no 1500 mm turning circle in the accessible bath, lift lobby, or one bedroom |
| 139 | `ACCESS.THRESHOLD` | GAP — a threshold over 12 mm on the accessible route |
| 140 | `ACCESS.RAMP_SLOPE` | GAP — a ramp steeper than 1:12 on the route |
| 141 | `ACCESS.CORRIDOR_WIDTH` | GAP — under 1050-1200 mm on the accessible route, above the 900 mm NBC floor |
| 142 | `ACCESS.ROUTE_CONTINUOUS` | GAP — no step-free route from the entrance to one bedroom, one bath and the kitchen |

## STACK — wet grouping (2 GAP)

`P.WET_GROUPING` is stated as a principle and cites only
`DESIGN.KITCHEN_FAR_FROM_PARKING`, which is not about stacking at all. Nothing
in the codebase mentions plumbing stacks. The three vertical-alignment rules
this family used to hold are in `DORMANT`: the output has one floor.

| # | ID | Rule |
|---|---|---|
| 143 | `STACK.WET_NOT_GROUPED` | GAP — kitchen, baths and utility do not share walls or a plumbing line |
| 146 | `STACK.DRAIN_NO_EXTERIOR_WALL` | GAP — a kitchen sink or bath drain with no exterior or shaft wall to run to |

`EXIST` — programme completeness — had five rules and all five are now in
`DORMANT`, so the family has no live section. It survives as a family label on
#170.

## ACCESSGRAPH — remaining door-graph gaps (3 GAP)

| # | ID | Rule |
|---|---|---|
| 153 | `ACCESSGRAPH.POOJA_UNDER_STAIR` | GAP for the vertical case; the **planar** case is now live — `pooja`-`stair` is a prohibition in `typology._COMMON` and `topology.UNIVERSAL`, so #71 reports a shrine sharing a door or wall with the stair. Directly *under* it still needs the second floor (`DORMANT`) |
| 154 | `ACCESSGRAPH.SERVICE_CROSSES_GUEST` | GAP — the help's route to the kitchen crosses the guest route from the front door |
| 156 | `ACCESSGRAPH.BEDROOM_DOOR_COUNT` | GAP — a bedroom with more doors than circulation plus one bath plus one balcony, tested directly instead of inferred from #43 |

## ENV — light and air gaps (2 GAP)

| # | ID | Rule |
|---|---|---|
| 157 | `ENV.NO_CROSS_DRAUGHT` | GAP — inlet and outlet on the same face, so nothing crosses; #61 only proxies this |
| 158 | `ENV.NO_LIGHT_SHAFT` | GAP — an interior habitable room with no possible window and no shaft inserted. Measured on the suite: 17 plans, 22 rooms, including two windowless kitchens (`det-46`, `det-47`) and a landlocked dining (`det-28`, `furn-01`) |

## DIM — dimensional gaps (2 GAP)

| # | ID | Rule |
|---|---|---|
| 159 | `DIM.KITCHEN_COUNTER_RUN` | GAP — no continuous 1800 mm counter run left in the kitchen once doors and windows are placed |
| 161 | `DIM.DOOR_SWING_COLLISION` | GAP — door swings colliding with each other or with fixtures; the furnisher rejects such poses but no rule reports it |

## SITE — plot-level gaps (1 GAP)

| # | ID | Rule |
|---|---|---|
| 163 | `SITE.DRIVEWAY_DISCONTINUOUS` | GAP, **error** — no continuous 3000 mm drive from gate to parking. Review: must never happen; in 13 suite plans the drive stops ~4 m short of its own gate |

## DORMANT — 22 GAP rules nothing in the current output can trip

Not retired and not wrong: each is a rule we would want, and each is unstateable
or unfireable today because the input it needs does not exist. They are down
here so the families above read as the live checklist. The blocking reason is
recorded per rule, verified against the code and against all 89 suite plans, so
each becomes actionable the moment its blocker clears.

Three blockers cover all 22:

1. **Single-storey output.** All 89 suite plans are ground floor plans. `spec.py`
   carries `storeys` and uses it for FAR and coverage arithmetic, but no second
   floor of geometry is ever produced, so nothing vertical can be compared.
   Blocks #114, #135, #144, #145, #147, #151.
2. **An object the system has no type for.** No parking room appears in any plan
   (#117, #123, #150, #160), no stair (#115, #134), no open-to-sky well (#113),
   no portico (#108), no meter room (#155), and no sump, tank or septic (#152,
   #162).
3. **A type collapse in `bridge.py`.** `dress` becomes `store`, `servant`
   becomes `bedroom`, `terrace` becomes `patio`. The rule can be written but not
   evaluated, because the distinction it depends on is gone before rules run.
   Blocks #105, #116, #119, #149. This is the same class of problem as the
   `foyer` / `passage` collapse in gating item 1 below, one layer further on.

| # | ID | Rule | Blocked by |
|---|---|---|---|
| 105 | `PURPOSE.DRESS_OFF_CIRCULATION` | GAP — a dressing room entered from a passage rather than from its bedroom is a store | needs the type split — `bridge.py:27` maps `dress` onto `store`, so a dressing room is indistinguishable from a store by the time rules run |
| 108 | `PURPOSE.PORTICO_NO_VEHICLE` | GAP — a portico no car can reach is a roof over nothing | no portico object — `portico` exists only as a label string in `topology.py:275`, never as a room type |
| 113 | `PURPOSE.OTS_TOO_NARROW` | GAP — an open-to-sky light well too narrow to light the rooms that face it | no OTS type — `roomtypes.py` has no open-to-sky well; 0 of 89 suite plans contain one |
| 114 | `PURPOSE.SHAFT_NOT_CONTINUOUS` | GAP — a shaft that does not line up floor to floor, so it ventilates nothing | single-storey output — every one of the 89 suite plans is a ground floor plan, so there is no second floor to align against |
| 115 | `PURPOSE.STAIR_TO_NOWHERE` | GAP — a stair whose top or bottom lands in no usable room | no stair in the output — stairs are `Stair` objects, and 0 of 89 suite plans carry one |
| 116 | `PURPOSE.TERRACE_VIA_BEDROOM` | GAP — a terrace reached only through a private room | needs the type split — `bridge.py:30` maps `terrace` onto `patio`, so a terrace is indistinguishable from a patio |
| 117 | `PURPOSE.PARKING_UNREACHABLE` | GAP — no car route from the gate to the parking space | no parking room — the `parking` type exists but appears in 0 of 89 suite plans; only the driveway furniture is drawn |
| 119 | `PURPOSE.SERVANT_ROOM_FAR_FROM_KITCHEN` | GAP — the help's room across the house from where the work is | needs the type split — `bridge.py:22` maps `servant` onto `bedroom`, so the help's room is indistinguishable from a bedroom |
| 123 | `REACH.KITCHEN_FROM_PARKING` | GAP — the grocery walk along the actual path, replacing #56's straight line | no parking room — see #117 |
| 134 | `EGRESS.STAIR_WIDTH` | GAP — exit stair under 1000 mm for residential below 15 m (#29 checks 900 mm, the room rule, not the exit rule) | no stair in the output — see #115 |
| 135 | `EGRESS.SECOND_EXIT` | GAP — no second means of escape where the storey count requires one | single-storey output — see #114 |
| 144 | `STACK.WET_NOT_ALIGNED` | GAP — wet rooms do not sit above one another across floors | single-storey output — see #114 |
| 145 | `STACK.WC_OVER_FOOD` | GAP — a toilet directly above a kitchen, dining or pooja room | single-storey output — see #114 |
| 147 | `STACK.OHT_OVER_BEDROOM` | GAP — the overhead tank sits above a bedroom rather than the wet stack | single-storey output, and no overhead-tank object of any kind |
| 148 | `EXIST.SCENARIO_GAP` | GAP — a room named in `Scenario.expects` is missing and nothing reports it | not a plan rule — `Scenario.expects` is a field no code reads, so this is a code-hygiene item, not a judgement about a drawing |
| 149 | `EXIST.SERVANT_NO_TOILET` | GAP — a servant room with no toilet of its own | needs the type split — see #119 |
| 150 | `EXIST.NO_TWO_WHEELER` | GAP — no two-wheeler space on a plot plan, which most Indian households need | no parking room — see #117; `two_wheeler` exists only as a furniture catalogue entry (`catalog.py:314`) |
| 151 | `EXIST.NO_LIFT` | GAP — a G+2 or an accessibility brief with no lift | single-storey output — see #114; and no lift object |
| 152 | `EXIST.SITE_SERVICES_MISSING` | GAP — no sump, overhead tank, septic tank or soak pit on a plot with no municipal alternative | no site-service objects — nothing in the system models a sump, overhead tank, septic tank or soak pit |
| 155 | `ACCESSGRAPH.METER_ROOM_INTERNAL` | GAP — the meter room cannot be read without entering the house | no meter-room type — nothing in the system models one |
| 160 | `DIM.PARKING_BAY` | GAP — a parking space under 2500 x 5000 mm per car | no parking room — see #117 |
| 162 | `SITE.SUMP_SEPTIC_PROXIMITY` | GAP — sump and septic tank closer than the 7500 mm separation practice requires | no site-service objects — see #152 |

**Deliberately not moved here.** `ACCESS.*` (#137-142) also fires on nothing
today, but by design: it is gated on a brief flag and must stay silent until
someone asks for an accessible plan. It is dormant by configuration, not
blocked, so it stays in its family. The same goes for the implemented rules that
happen to have nothing to bite on — #56 and #89 need parking and a stair
respectively, and #29 needs a stair, but they are live code and belong where
they are.

---

## REVIEW — rules the suite review added (12 GAP)

Every line here comes from a human review of the 89 plans in `out/suite_svg`
(`FLOORPLAN_ISSUES.md`). They are stated as general rules, not as observations
about particular plans, and each says why the existing rules did not already
cover it. Numbered from 164 so nothing above renumbers; the family each belongs
to is named so they can be folded in when the families are next touched.

| # | ID | Sev | Family | Rule |
|---|---|---|---|---|
| 164 | `DESIGN.ENTRY_INTO_CIRCULATION_NO_FOYER` | warn | DESIGN | GAP — the front door opens into a passage or corridor rather than into a living, hall or foyer. Entering straight into the living is normal and correct; what needs a named arrival space is a door that lands in circulation. Replaces the blanket form of #51 |
| 165 | `TOPO.MASTER_NO_ATTACHED_BATH` | warn | TOPO | GAP — the plan has two or more bathrooms and none of them is attached to the master bedroom. Silent on single-bathroom plans, where a shared bath is the correct answer, not a compromise. 79 suite plans leave the master unattached, but only 28 have two or more baths and so trip this rule |
| 166 | `TOPO.BATH_BEHIND_BATH` | error | TOPO | **now live via #71** — added to `typology._COMMON` and `topology.UNIVERSAL`, so `TYPO.FORBIDDEN_ADJACENCY` reports it and the solver prices it. #43 could not catch it because it exempts wet rooms as destinations. Measured: 0 of 200 real ResPlan plans, 13 of 89 of ours |
| 167 | `DESIGN.KITCHEN_NOT_ON_PUBLIC` | error | DESIGN | GAP for the access half — the kitchen has no door to a living or dining space; 23 suite plans reach it only via a bedroom, utility or pooja. The *door* half is now live: `kitchen`-`bedroom` is a prohibition in both tables, reported by #71. Measured: 3 of 200 real ResPlan plans (1.5%), 24 of 89 of ours (27%) |
| 168 | `DESIGN.MULTIPLE_WC_ON_PUBLIC` | warn | DESIGN | GAP — two or more toilet doors open into the living or dining. Exactly one is expected and correct: that is the common WC. The defect is the second one, not the first, so #52 / #53 should not fire on a single public WC |
| 169 | `DESIGN.ROOM_ASPECT` | warn | DESIGN | GAP — a habitable room exceeds the aspect limit its type carries in `roomtypes.py`. A preference, deliberately soft: a tight envelope can force a long room and that is accepted. No rule reads the limit today; 43 suite plans hold 62 rooms at 2.0 or worse |
| 170 | `EXIST.NO_DINING_AND_LIVING_TOO_SMALL` | warn | EXIST | GAP — the plan has no dining space *and* the living is too small to seat one. A small house is right to fold dining into the living; the defect is only when neither space can hold a table. Absence of a dining room on its own is not a finding |
| 171 | `ENV.HABITABLE_NO_EXTERIOR_WALL` | error/warn | ENV | GAP — a habitable room touches no exterior wall. Error for a sleeping or living room and for a kitchen; warn for a study or family room, which can live off borrowed light. The geometric converse of #60, and the precondition for #158 |
| 172 | `PURPOSE.PRIMARY_BALCONY_VIA_SERVICE` | warn | PURPOSE | GAP — the plan's primary balcony is reachable only through the kitchen or a utility. A second, service balcony off the kitchen is correct and expected — that is the drying yard. Only the main balcony has to open off a living or a bedroom |
| 173 | `SITE.DRIVEWAY_OVER_BUILDING` | error | SITE | GAP — the drive or car porch slab overlaps the building footprint. Not a design judgement: it means the object was never placed. 71 suite plans do it, 13 of them by over 20 m². Usually fires together with #175 |
| 174 | `SITE.GATE_NOT_FACING_ENTRANCE` | warn | SITE | GAP — the gate sits on a boundary the front door does not face, so the approach has to wrap the building. Distinct from #118, which asks whether the gate faces a road at all; this asks whether gate and door agree. 13 suite plans put the gate on the rear line with the door on the front wall |
| 175 | `SITE.DRIVEWAY_OFF_PLOT` | error | SITE | GAP — the drive or car porch crosses the plot boundary. Nothing the plan draws may sit on land the plot does not own, the road-facing apron included: that is the road authority's, not the owner's. 71 suite plans do it, in two distinct shapes — 58 push the porch ~1 m² over the *front* line, and 13 push 8-10 m² over a *side* line, the latter being the same unplaced-slab bug as #173 |

### Rejected — reviewed and deliberately not rules

Recorded so they are not proposed again. Each was raised by the review and
turned down with a reason.

| Not a rule | Why |
|---|---|
| The living room carries all the circulation | That is what a living room is for in an Indian plan. Four or five doors on the living is normal, and #80 already *wants* the living to be the most integrated space |
| The front door opens straight into the living | Normal and usually preferable to a lobby. Only #164's narrower case is a defect |
| A balcony has no window | A balcony is open by definition; it needs an open edge, which is #67. A window test is the wrong instrument |
| Every toilet is behind a bedroom | A preference, already #77 at `warn`. The hard version is #46 |
| A toilet door opens into the living | One is the common WC and belongs there. See #168 for the real defect |
| The plan has no dining room | Fine when the living can absorb it. See #170 |
| A kitchen has no window | Acceptable when it opens onto a utility that has its own exterior opening — an amendment to #25, not a new rule |

---

## Three things gate the gaps, in this order

1. **Split the collapsed room types.** `roomtypes.py` aliases `passage`,
   `corridor`, `hallway`, `lobby` and `circulation` all to `foyer`, so a rule
   saying "a foyer sits at the front door" fires on every corridor — that is why
   #101 and #164 are still gaps. `bridge.py` collapses three more the same way
   (`dress`→`store`, `servant`→`bedroom`, `terrace`→`patio`), which is what
   blocks #105, #116, #119 and #149 in `DORMANT`. Nine further collapses and
   twelve missing types are listed in `SPACES.md` §2.
2. **Build the walkable graph.** All ten `REACH.*` rules and #45 / #56 need it,
   and nothing in the system provides it. Reference: the BITS Pilani
   circulation-graph paper in `SPACES.md` §7.
3. **Merge `typology.py` into `topology.py`.** Both run inside `validate()`
   from tables that disagree — concretely: the solver reads
   `topology.Scenario.prefs` while `TYPO.FORBIDDEN_ADJACENCY` reads
   `typology.Typology.expect`, so the three prohibitions the suite review added
   had to be written into both files or the solver would avoid a defect the
   validator could not name; `score.py` already says topology supersedes
   typology, but nothing deleted it. Adding a rule today means choosing one of
   three homes — `typology.TYPOLOGIES`, `topology.SCENARIOS`,
   `principles.PRINCIPLES` — and the other two silently drift.
