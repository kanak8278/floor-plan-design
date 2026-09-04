# The space catalogue

Every space that can appear in an Indian home, what it is *for*, and where it has
to sit to do that job. This is the source document for the relational rules;
`bylaws.py` and `standards.py` hold the numbers, `roomtypes.py` the taxonomy,
`topology.py` the preference matrix.

Written because the rule set grew rule-first: 97 checks exist, but nobody ever
wrote down the full list of spaces and their purposes, so the gaps are invisible.
A rule you never thought of looks exactly like a rule that passes.

---

## 1. The five questions

Every space answers five questions. Our 97 existing rules answer questions 1 and
3 well, question 5 partly, and questions 2 and 4 **not at all**.

1. **Existence** — should this space be in the programme at all, given typology
   and size band? (`BRIEF.ROOM_MISSING`, `DESIGN.MULTIPLE_KITCHENS`, `expects`)
2. **Justification** — given that it exists, does its *position* let it serve its
   purpose? A foyer away from the front door is not a bad foyer, it is not a
   foyer. This is the missing family. It is not the inverse of (3): a room can
   have every required door and still be pointless.
3. **Access and privacy** — who can reach it, through what, and who must not
   have to. (`TOPO.*`, `DESIGN.BEDROOM_THROUGH_TRAFFIC`, `SYNTAX.*`)
4. **Reach** — how far is the *walk*, along the circulation, in metres, to the
   things this space depends on. Also missing. Everything today is either a
   door-graph hop count or a straight-line polygon distance through walls.
5. **Envelope contact** — light, air, plumbing line, exterior wall, orientation.
   (`NBC.VENTILATION_*`, `DESIGN.NO_WINDOW`, `DESIGN.SINGLE_ASPECT`, `VASTU.*`)

Two rule families are absent outright and are not (1)-(5) at all:

* **Egress** — NBC 2016 Part 4. Travel distance to an exit, dead-end length
  (3 m unsprinklered / 6 m sprinklered), 1000 mm exit doorway, 1000 mm stair
  for residential under 15 m. Zero checks today.
* **Accessibility** — RPwD Harmonised Guidelines 2021. 900 mm clear door
  (800 mm absolute floor), 1500 mm turning circle, ≤ 12 mm threshold. Zero
  checks today. Matters commercially: an ageing-parents brief is common.

---

## 2. Vocabulary problem to fix first

`roomtypes.py` has 18 types and resolves by substring alias. Several *distinct
purposes* are collapsed into one type, which makes their rules unstateable:

| Collapsed into | Should be separate | Because |
|---|---|---|
| `foyer` | `passage`, `lobby`, `lift_lobby` | A foyer is an arrival buffer at the front door. A passage is a distributor anywhere. A rule "foyer at depth ≤ 1" fires on every corridor. |
| `foyer` (short alias `lift`) | `lift` | A lift is a vertical element with a shaft, not a room. |
| `bathroom` | `wc`, `powder`, `servant_toilet` | Different minima, different access rules, different Vastu treatment. `wc` is already in `SUBTYPE_OF` with no `RoomType` behind it. |
| `store` | `dress`, `understair_store`, `shoe_store` | A walk-in dress opens off a bedroom; a dry store opens off the kitchen. Opposite rules. |
| `utility` | `laundry`, `dry_balcony` | A dry balcony must be outdoor with a perimeter edge; a utility need not. |
| `patio` | `terrace`, `courtyard`, `ots` | Terrace is at roof level, courtyard is enclosed, OTS is a double-height void. Different FAR treatment. |
| `sitout` | `porch`, `portico`, `verandah`, `balcony_service` | Portico is vehicular and abuts the drive; a sitout is occupiable. |
| `parking` | `car_porch`, `garage`, `two_wheeler` | Covered vs enclosed changes ground coverage and FAR. |
| — | `family_living`, `servant_room`, `home_theatre`, `gym`, `bar_pantry`, `mezzanine`, `refuge`, `meter_room` | Absent entirely. `family_living` is the whole point of the villa typology and there is no type for it. |

Site elements that are not rooms and have no home today: compound gate,
compound wall, driveway, sump, overhead tank, septic tank, soak pit, RWH pit,
gas cylinder bay, AC ledge, DB/meter board, ramp, plinth steps.

---

## 3. The catalogue

Format for each entry:

> **name** — *purpose in one line*
> **Position:** where it has to be. **Reaches:** what the walk must be short to.
> **Never:** hard conflicts. **Pointless when:** the justification failure.

### 3.1 Arrival sequence

**compound_gate** — *the threshold between road and plot; the plot has no way in without it.*
Position: on the boundary edge facing the declared road, aligned with the front door and the drive so gate, drive and door read as one axis. Car gate 3000 mm, pedestrian 1000 mm. Reaches: driveway (contiguous), front door. Never: on a boundary that faces no road. Pointless when: there is no boundary wall, or a second gate duplicates the first with no separate use (service gate is a legitimate second).

**compound_wall** — *defines the plot and secures it.*
Position: the full boundary except gate openings. Never: crossing a setback line inward.

**driveway** — *load path from gate to porch for a car.*
Position: continuous 3000 mm minimum from gate to porch/parking, inside the front setback. Reaches: gate to parking with no step. Never: through landscape it cannot support, or requiring a 3-point turn. Pointless when: it does not connect the gate to a parking space.

**parking / car_porch / garage** — *keeps the car inside the compound, under cover, and near the door.*
Position: front or side setback, off the drive, on the road side. `car_porch` covered and open-sided; `garage` enclosed and counts differently for coverage. 2500 x 5000 mm per car minimum. Reaches: **front door short**, and **kitchen short — groceries are carried by hand** (today: `DESIGN.KITCHEN_FAR_FROM_PARKING`, straight-line 12 m). Never: blocking the front door swing. Pointless when: unreachable from the gate by a car.

**two_wheeler** — *scooters, which every Indian household has and no plan draws.*
Position: 900 x 2000 mm alongside the drive or under the stair overhang.

**plinth_steps / ramp** — *gets you from ground to floor level.*
Position: at the front door and any external door. Ramp 1:12 max where accessibility is asked for. Never: a step inside the door swing. Pointless when: a ramp exists but the door it serves is under 900 mm clear.

**sitout / verandah** — *outdoor room you actually sit in; shade buffer before the front door; where the milk and the courier are received.*
Position: front or side, covered, with a perimeter edge — **it must have open sides or it is a room**. Depth ≥ 1800 mm to be occupiable. East or north preferred (morning sun, no afternoon heat). Reaches: front door directly; gate visible from it. Never: fully enclosed on all sides (today: `DESIGN.BALCONY_ENCLOSED` covers this for balconies). Pointless when: it has no external edge, or you cannot see the gate from it, or it is under 1800 mm deep.

**portico** — *covered vehicular drop-off; a sitout you drive under.*
Position: abutting the driveway head, roof over, 2400 mm clear height minimum for a car. Never: treated as occupiable seating area. Pointless when: no vehicle can reach it.

**foyer / vestibule** — *arrival buffer: you take your shoes off, the door does not open into the seating, and a guest at the door does not see the whole house.*
**Position: at door-depth 0 or 1 from the front door. This is the whole rule.** 1.8-8 m² (`entrance.FOYER_MIN/MAX_M2`). Reaches: living directly. Never: deeper than 1 hop from the front door; never a through-route to bedrooms only. **Pointless when: it is not the first space inside the front door — then it is a passage and should be relabelled or deleted.** Also pointless when carpet area is below the typology threshold (`entrance.FOYER_CARPET_FLOOR_M2`).

**shoe_store** — *the shoe rack that exists in every Indian home and appears in no plan.*
Position: in the foyer or in the first 1200 mm inside the front door. 300 mm deep x 900 mm run.

### 3.2 Public zone

**living / hall** — *receives guests, seats the family, and is the configurational core the rest of the plan hangs off.*
Position: the most integrated space in the door graph (`SYNTAX.LIVING_NOT_CORE`, real plans 97%). Front half of the plan, on the road side, north or east. Reaches: foyer, dining, stair, **a WC without entering a bedroom**. Never: a bedroom door opening into it at villa scale (`Pref("living","bedroom",-1.0)`); never the only route between two bedrooms. Pointless when: it is not the core — something else is organising the house.

**family_living / TV lounge** — *the informal living the family uses daily, so the formal living can stay presentable.* **No `RoomType` exists for this.**
Position: deeper than the formal living, adjacent to the bedroom wing or on the upper floor. Reaches: bedrooms, dining. Never: on the guest route from the front door. Pointless when: it is not separated from the formal living — then you built one living room twice.

**dining** — *the family eats here; food is carried from the kitchen and plates carried back.*
Position: between kitchen and living. Reaches: **kitchen directly, serving distance** (`Pref("dining","kitchen",1.0)`); living open or near. Never: adjacent to, sharing a wall with, in view of, or below a WC (`DESIGN.DINING_ABUTS_WC`, `DESIGN.WC_VISIBLE_FROM_DINING`, rejected outright in Indian practice). Pointless when: the walk from the kitchen counter to the table crosses the living room.

**pooja / mandir** — *daily worship; a shrine, not a store.*
Position: north-east; idol faces west so the worshipper faces east. Enclosed room ≥ 1500 x 2100 mm (5 x 7 ft is the cited comfortable minimum for 2-3 people); a niche is acceptable in a compact unit. Reaches: circulation; near the foyer is traditional. Never: sharing a wall or door with a toilet; never under a staircase; never in a bedroom; south or south-west avoided. Not habitable for NBC ventilation — do not demand glazing. Pointless when: there is no wall left for the mandir after doors and windows.

**guest_wc / powder** — *a toilet a visitor can use without entering the private half of the house.*
Position: off the foyer or the living, **door-depth ≤ 2 from the front door**, door not in view of the dining table. WC + basin only, no shower, ~1200 x 1800 mm. Reaches: **living, short walk — this is the check that catches "all the washrooms are at one place".** Never: opening into kitchen or dining. Pointless when: it is deeper in the plan than the nearest bedroom's bath — a guest will use that instead.

**home_theatre / bar_pantry / gym** — *programme rooms a large brief asks for.* No types exist.
Theatre: no windows wanted, deep in the plan, away from bedrooms (noise). Bar: off dining or family living. Gym: perimeter, cross-ventilated, near a bath.

### 3.3 Private zone

**master_bedroom** — *the primary suite; the most private room in the house.*
Position: south-west (Vastu), deepest quarter of the plan, off a passage not off the living. ≥ 12 m², min width 2700 mm. Reaches: its own attached bath; dress if asked; a balcony if asked. Never: a through-route to any other room (`DESIGN.BEDROOM_THROUGH_TRAFFIC`); never a door into the formal living at villa scale; never sharing a wall with a lift shaft or the stair flight. Pointless when: it is not the largest or most private bedroom — then it is mislabelled.

**bedroom** — *one household member sleeps, dresses and stores clothes here.*
Position: off circulation, clustered with the other bedrooms — the private zone reads as one contiguous cluster (`ZONE.PRIVATE_FRAGMENTED`, `DESIGN.BEDROOMS_SCATTERED`). ≥ 7.5 m² (NBC), min width 2400 mm. Reaches: a bath within 1 hop. Exactly 1 door to circulation, plus at most 1 to its own bath and 1 to its own balcony — that is the measured real-plan signature (behind a private room in real ResPlan plans: bathroom 416, balcony 232, kitchen 6, living 1). Never: 3+ doors; never a corridor. Envelope: needs an exterior wall and a window; 1800 mm of clear wall for a wardrobe (`DESIGN.NO_WARDROBE_WALL`).

**kids_bedroom / guest_bedroom** — *subtypes; different position, same rules.*
Kids: adjacent to master, sharing a bath is acceptable (Jack-and-Jill). Guest: nearest the public zone, ideally with a bath reachable from circulation too, so it doubles as the common bath when empty.

**study / home_office** — *work; needs quiet and a window.*
Position: north or east light, off circulation not off a bedroom unless it is the master's. Never: the only route to anything.

**dress / walk_in** — *clothes storage adjoining the bedroom that uses it.*
Position: **between bedroom and its bath, entered from the bedroom only.** ≥ 1200 mm clear between hanging runs. Never: entered from circulation. Pointless when: it opens off a passage — then it is a store, priced and placed differently.

**bathroom (attached)** — *en-suite, private to one bedroom.*
Position: **exactly one door, from one bedroom** (`kind="attached"`). Stacked with the other wet rooms. 2.8 m² NBC floor; real distribution p5 2.38 / median 4.64 / p95 7.43 m²; 15-40% of its bedroom's area. Never: two bedroom doors and no circulation door unless the brief asked for a Jack-and-Jill; never a door from circulation as well unless the brief asked for "common but attached". Never in the north-east.

**bathroom (shared_attached / Jack-and-Jill)** — *one bath, two bedrooms, no circulation door.*
Position: between exactly two bedrooms. Never: three or more bedrooms (`TOPO.BATH_OVERSHARED`). Pointless when: one of the two bedrooms also has its own attached bath.

**bathroom (common)** — *serves the household and guests without entering a bedroom.*
Position: off circulation, reachable from the public zone. Reaches: **living, and every bedroom that has no attached bath, by a short walk.** Never: the sole bath reachable only through a bedroom (`DESIGN.SOLE_BATH_VIA_BEDROOM`). Pointless when: it sits at the end of the bedroom wing and the living room has nothing closer — the guest-coverage failure.

**servant_toilet** — *separate WC for domestic help, standard in Indian plans.* No type.
Position: off the utility or the service yard, accessed without entering the house. Never: opening into kitchen or utility directly.

### 3.4 Service zone

**kitchen** — *cooking; the busiest room in an Indian house and the one with the most constraints.*
Position: south-east (Vastu), on the perimeter with a window and an exhaust path. ≥ 5.0 m², min width 1800 mm. Reaches: **dining directly; utility directly; dry store within reach; parking short (groceries)**; sink on an exterior wall for drainage. Two ways in and out is better than one (`DESIGN.KITCHEN_SINGLE_ACCESS`). Work triangle: each leg 1200-2700 mm, perimeter 4000-8000 mm. Never: a WC opening into it (`NBC.WC_OPENS_INTO_KITCHEN`, hard); never under a toilet on the floor above; never the only route to a bedroom; never north-east or south-west. Needs continuous counter run — do not fill every wall with openings.

**utility / wash_area** — *the washing machine, the gas cylinder, the wet mop, the drying line.*
Position: **directly off the kitchen, on the rear or side, with an exterior edge for drainage and drying.** ≥ 3.5 m² (contents-driven: 600 x 650 machine + 900 mm access). North-west. Reaches: kitchen (1 hop, mandatory), servant toilet if any. Never: opening into the dining or living; never interior with no drainage. **Pointless when: it is not adjacent to the kitchen — then the laundry crosses the house.**

**dry_balcony** — *hangs the washing outside.*
Position: off the utility, outdoor, perimeter edge, gets sun. Pointless when: enclosed or north-facing with no sun.

**store (dry store)** — *bulk provisions, rice, oil; the Indian larder.*
Position: **off the kitchen or within a few metres of it** (`DESIGN.STORE_FAR_FROM_KITCHEN`). South-west. No window needed, not habitable. Pointless when: it is on the far side of the house from the kitchen — then it is a general store, which is a different (legitimate) thing that should be relabelled.

**general_store** — *suitcases, seasonal things.*
Position: anywhere off circulation, under the stair is ideal. Never: only reachable through a bedroom.

**understair_store** — *the space under the flight, which is otherwise wasted.*
Position: under the stair. Never: a pooja room (explicit Vastu prohibition), never a toilet with the flight above.

**servant_room / help_room** — *live-in domestic help.* No type exists.
Position: near the kitchen and utility — the help works there. West side (Vastu); south-east acceptable; not north, east, or south-west. Reaches: kitchen, utility, its own toilet. Never: on the guest route; never entered through a family bedroom. Needs its own toilet or the arrangement fails.

**laundry** — *distinct from utility in larger houses.*
Position: near the bedroom wing or off the utility; needs drainage and drying access.

**meter_room / DB** — *electrical panel and meter, required by the utility.* No type.
Position: at the boundary or in the foyer/porch, accessible from outside without entering the house. Never: in a bathroom or above a sink.

**gas_cylinder_bay** — *LPG cylinders must be outside and ventilated.*
Position: in the utility or service yard, at ground level, ventilated at floor level. Never: enclosed interior, never below ground.

**ac_ledge** — *outdoor units need somewhere to sit that is not the balcony you use.*
Position: rear or side, one per bedroom served, reachable for service. Never: blocking a window or discharging into another room's inlet.

### 3.5 Water and sanitation (site, not rooms)

**sump** — *underground water storage, mandatory in Bengaluru practice.*
Position: front or side setback, accessible for cleaning, away from the septic tank. Capacity from occupancy. Never: under the building footprint if it can be avoided; never adjacent to the septic tank.

**overhead_tank (OHT)** — *gravity head for the taps.*
Position: on the roof above a load-bearing line, above the wet-room stack so the down-take is short. Never: over a bedroom (leak risk and noise).

**septic_tank / soak_pit** — *on-site sewage where there is no sewer.*
Position: rear setback, minimum separation from sump and from the boundary, accessible for pumping. Never: within a few metres of a water source.

**rwh_pit** — *rainwater harvesting, mandatory above a plot-size threshold (`BYLAW.RWH_REQUIRED`).*
Position: where the roof downpipes land, in a setback, with a filter chamber.

### 3.6 Circulation and vertical

**passage / corridor** — *distributes to rooms that would otherwise open off each other.*
Position: spine of the private zone, ≥ 900 mm clear (`NBC.PASSAGE_WIDTH`), 1050-1200 mm if accessibility is asked for. Reaches: every bedroom, the common bath, the stair. Never: a dead end longer than a room's depth (`DESIGN.DEAD_END_CIRCULATION`); never lit only borrowed light if it runs over ~6 m. **Pointless when: it serves fewer than two rooms — a passage to one room is a wasted door's width of carpet.** Efficiency: total circulation should be roughly 10-15% of carpet in a house; above ~20% the plan is corridor-heavy.

**foyer** — see 3.1. A passage is not a foyer.

**staircase** — *vertical circulation; in a duplex it is the whole organisation of the plan.*
Position: **rises from the hall or a landing, never from inside a bedroom or a kitchen.** South or west (Vastu), not north-east. 900 mm minimum width (NBC residential; 1000 mm for exit stairs under 15 m), riser ≤ 190 mm, tread ≥ 250 mm, landing every ~12 risers, 2R+T comfort rule. Reaches: hall below, landing above serving bedrooms. Never: landing inside a bedroom (`Pref("stair","bedroom",-0.8)`); never a winder without a landing where the code requires one; never above the pooja room. Pointless when: the house is single-storey and it leads only to a roof with no use.

**landing** — *turns the flight and gives the upper floor a distributor.*
Position: at every direction change and at each floor. Reaches: at least two rooms upstairs, or it is a passage. Never: a door swinging onto a flight.

**lift** — *vertical access in a G+2 or where accessibility is asked for.* No type.
Position: adjacent to the stair, in a shaft continuous through all floors, lobby ≥ 1500 x 1500 mm in front. Never: sharing a wall with a bedroom head; never a shaft that stops short of a floor it serves.

**lift_lobby** — *the space in front of the lift.*
Position: at every floor the lift serves, ≥ 1500 mm clear. Pointless when: it is under 1500 mm and a wheelchair cannot turn.

**shaft / duct (PHE, HVAC, electrical riser)** — *carries pipes and cables vertically; also the light source for an interior wet room.*
Position: **continuous through every floor it serves, aligned floor to floor.** Adjacent to the wet stack. Never: offset between floors; never the only ventilation for a habitable room (a shaft ventilates a bath, not a bedroom). Pointless when: it does not align with the shaft above or below — then it is a hole.

**ots / light_well / courtyard** — *brings light and air into the middle of a deep plan; the Indian answer to an interior room.*
Position: at the centre of a deep plan, open to sky, minimum ~2400 x 2400 mm to actually light the rooms around it. Reaches: the rooms that would otherwise be windowless. Never: covered over. Pointless when: too narrow to light anything, or the rooms around it have no windows onto it. **This is the missing tool for the ~10 suite plans with interior habitable rooms.**

**terrace** — *usable roof.*
Position: roof level, with parapet ≥ 1000 mm, reached by the stair. Never: reached only through a bedroom.

**mezzanine / loft** — *half-floor above a tall space; storage or a bed.* No type.
Position: over a space with ≥ 4200 mm clear below. Area capped by bylaw as a fraction of the room below. Never: counted as a habitable room if the head height is under NBC's minimum.

**refuge_area** — *required above a height threshold in high-rise; not our current scope but in the vocabulary.*

### 3.7 Outdoor occupiable

**balcony** — *outdoor extension of the room it belongs to; sold as amenity.*
Position: **off the room that hosts it, on a perimeter edge, open on at least one long side.** ≥ 1200 mm deep to be usable (a 700 mm balcony is a ledge). Reaches: its host room directly. Never: enclosed on all sides (`DESIGN.BALCONY_ENCLOSED`); never a through-route between two rooms (`DESIGN.BALCONY_THROUGH_ROUTE`); never hosted by a bathroom or a store (`DESIGN.BALCONY_ODD_HOST`); never with no door at all (`DESIGN.BALCONY_NO_ACCESS`). **An interior balcony is not a balcony** — priced with the habitable rooms in the solver for exactly this reason.

**patio** — *ground-level paved outdoor room.*
Position: rear or side, off the living or dining, private from the road.

**landscape / lawn** — *what is left of the plot, and the setback the bylaw forces.*
Position: setbacks. Reaches: nothing. Never: the only route to the rear of the plot if a service route is needed.

---

## 4. What each purpose implies as a check

Grouped by the five questions plus the two absent families. Status is against the
code as it stands: **[have]** implemented, **[part]** implemented but blind to the
case above, **[gap]** not implemented.

### Q1. Existence and count

| # | Rule | Status |
|---|---|---|
| E1 | Every room the brief names exists in the plan | [have] `BRIEF.ROOM_MISSING` |
| E2 | Every room the typology expects exists, or the gap is declared | [part] `Scenario.expects` is data, nothing checks it |
| E3 | One kitchen per dwelling unit | [have] `DESIGN.MULTIPLE_KITCHENS` |
| E4 | Bathroom count matches the brief, by kind not just number | [part] `TOPO.ATTACHED_BATH_SHORTFALL` counts; cannot say *which* bedroom |
| E5 | Real storage exists (store or utility, not only wardrobes) | [have] `DESIGN.NO_STORAGE` |
| E6 | A servant room has a servant toilet | [gap] no type |
| E7 | A brief with a car has a parking space inside the compound | [part] via `BRIEF.ROOM_MISSING` only if asked |
| E8 | Two-wheeler space where the brief implies one | [gap] |
| E9 | Sump / OHT / septic / RWH present where bylaw or practice requires | [part] `BYLAW.RWH_REQUIRED` only |
| E10 | A G+2 or an accessibility brief has a lift | [gap] |

### Q2. Justification — does its position let it serve its purpose (the missing family)

Every one of these is a **[gap]**. Proposed prefix `PURPOSE.*`. The finding is not
"move a door", it is "this room is not doing its job; relocate it or delete it".

| # | Rule |
|---|---|
| J1 | `PURPOSE.FOYER_NOT_AT_ENTRY` — a foyer at door-depth > 1 from the front door is a passage. **[part] — tested: a foyer relabelled at depth 3 does fire `TYPO.ENTRY_SEQUENCE` + `DESIGN.DEAD_END_CIRCULATION` + `TYPO.MISSING_ADJACENCY`, all `warn`. The residual gap is severity and framing (the answer is 'delete it', not 'add a door'), plus the alias collapse: a second `foyer`-category passage deep in the plan is invisible because `ENTRY_SEQUENCE` takes the minimum depth over the category.** |
| J2 | `PURPOSE.FOYER_UNJUSTIFIED` — a foyer below the typology's carpet threshold spends contested area on a lobby |
| J3 | `PURPOSE.UTILITY_NOT_OFF_KITCHEN` — a utility not adjacent to the kitchen |
| J4 | `PURPOSE.STORE_NOT_OFF_KITCHEN` — a dry store far from the kitchen is a general store, mislabelled |
| J5 | `PURPOSE.DRESS_OFF_CIRCULATION` — a dress entered from a passage is a store |
| J6 | `PURPOSE.SITOUT_NOT_AT_ARRIVAL` — a sitout you cannot see the gate from, or that the front door does not open onto |
| J7 | `PURPOSE.SITOUT_TOO_SHALLOW` — under 1800 mm deep, nobody sits in it |
| J8 | `PURPOSE.PORTICO_NO_VEHICLE` — a portico no car can reach |
| J9 | `PURPOSE.PASSAGE_SERVES_ONE` — a corridor serving fewer than two rooms |
| J10 | `PURPOSE.POWDER_TOO_DEEP` — a guest WC deeper than the nearest bedroom's bath |
| J11 | `PURPOSE.FAMILY_LIVING_NOT_SEPARATED` — a second living on the guest route is the first living twice |
| J12 | `PURPOSE.BALCONY_TOO_SHALLOW` — under 1200 mm deep, it is a ledge |
| J13 | `PURPOSE.OTS_TOO_NARROW` — a light well too small to light the rooms around it |
| J14 | `PURPOSE.SHAFT_NOT_CONTINUOUS` — a shaft that does not align floor to floor |
| J15 | `PURPOSE.STAIR_TO_NOWHERE` — a stair with no used destination |
| J16 | `PURPOSE.TERRACE_VIA_BEDROOM` — a terrace reached only through a private room |
| J17 | `PURPOSE.PARKING_UNREACHABLE` — no car route from gate to parking |
| J18 | `PURPOSE.GATE_OFF_ROAD` — a gate on a boundary that faces no road |
| J19 | `PURPOSE.SERVANT_ROOM_FAR_FROM_KITCHEN` — the help's room across the house from where the work is |
| J20 | `PURPOSE.DRY_BALCONY_NO_SUN` — a drying balcony with no sun exposure |

### Q3. Access and privacy (door graph)

| # | Rule | Status |
|---|---|---|
| A1 | Every room has a door | [have] `GEO.UNREACHABLE_ROOM` |
| A2 | No private room is the only route to another room | [have] `DESIGN.BEDROOM_THROUGH_TRAFFIC` |
| A3 | Bedroom door count: 1 to circulation + ≤1 bath + ≤1 balcony | [part] via A2's exemptions |
| A4 | Attached bath has exactly one door, from one bedroom | [have] `topology.classify_bathroom` |
| A5 | A shared bath serves exactly two bedrooms | [have] `TOPO.BATH_OVERSHARED` |
| A6 | At least one bath reachable without entering a bedroom | [have] `TOPO.NO_COMMON_BATH`, `DESIGN.SOLE_BATH_VIA_BEDROOM` |
| A7 | Front door does not open into a bedroom or kitchen | [have] `DESIGN.ENTRANCE_INTO_PRIVATE` |
| A8 | Front door does not open straight into the seating | [have] `DESIGN.ENTRANCE_NO_BUFFER` |
| A9 | No WC opens into a kitchen | [have] `NBC.WC_OPENS_INTO_KITCHEN` (hard) |
| A10 | No WC opens into or is visible from the dining | [have] `DESIGN.DINING_ABUTS_WC`, `WC_VISIBLE_FROM_DINING` |
| A11 | Pooja shares no wall or door with a toilet | [have] `Pref("pooja","bathroom",-1.0)` |
| A12 | Pooja is not under a staircase | [gap] |
| A13 | Bedrooms do not open into each other | [have] `Pref("bedroom","bedroom",-0.5)` |
| A14 | Bedroom door does not open into the formal living at villa scale | [have] `DESIGN.BEDROOM_OFF_LIVING`, villa `Pref` -1.0 |
| A15 | Stair rises from hall, does not land in a bedroom | [have] duplex `Pref` |
| A16 | Kitchen has two ways in and out | [have] `DESIGN.KITCHEN_SINGLE_ACCESS` (warn) |
| A17 | No dead-end circulation | [have] `DESIGN.DEAD_END_CIRCULATION` |
| A18 | Nothing deeper than the typology's max depth from entry | [have] `DESIGN.TOO_DEEP` |
| A19 | Arrival sequence order holds | [have] `TYPO.ENTRY_SEQUENCE` |
| A20 | Servant route to kitchen does not cross the guest route | [gap] |
| A21 | Meter room reachable from outside without entering the house | [gap] |
| A22 | Living is the configurational core | [have] `SYNTAX.LIVING_NOT_CORE` |
| A23 | Private rooms deeper than public ones | [have] `SYNTAX.NO_PRIVACY_GRADIENT` |
| A24 | No private room functioning as circulation | [have] `SYNTAX.PRIVATE_ROOM_INTEGRATED` |
| A25 | Balcony is not a through-route | [have] `DESIGN.BALCONY_THROUGH_ROUTE` |

### Q4. Reach — walking distance along circulation (the second missing family)

All **[gap]**. Proposed prefix `REACH.*`. Requires one new primitive: a walkable
graph whose nodes are door thresholds and room centroids, edges weighted by
in-plan path length, so "distance" means *walk*, not `poly.distance()`.

| # | Rule |
|---|---|
| R1 | `REACH.WC_FROM_LIVING` — walk from the living seating to the nearest guest-accessible WC. **This is "the living room doesn't have a washroom".** |
| R2 | `REACH.KITCHEN_FROM_PARKING` — groceries, along the path, not straight-line |
| R3 | `REACH.DINING_FROM_KITCHEN` — serving distance, measured |
| R4 | `REACH.BATH_FROM_BEDROOM` — every bedroom without an en-suite, to its nearest bath |
| R5 | `REACH.PUBLIC_ZONE_SPAN` — living-to-dining walk against the plan diagonal. **This is "hall and living at two ends of the house".** |
| R6 | `REACH.BEDROOM_CLUSTER_SPAN` — bedroom-to-bedroom walk, metric version of `BEDROOMS_SCATTERED` |
| R7 | `REACH.UTILITY_FROM_KITCHEN` — the laundry walk |
| R8 | `REACH.MASTER_FROM_ENTRY` — a master bedroom too shallow is not private; too deep is a trek |
| R9 | `REACH.CIRCULATION_SHARE` — circulation area as a fraction of carpet; flag above ~20% |
| R10 | `REACH.TORTUOSITY` — path length / straight-line distance for the key pairs; a high ratio means a wall in the way |

### Q5. Envelope: light, air, plumbing, orientation

| # | Rule | Status |
|---|---|---|
| V1 | Every habitable room has an exterior wall | [have] solver key, `DESIGN.NO_WINDOW` |
| V2 | Glazing sized to NBC (1 m² per 10 m², kitchen ≥ 1 m²) | [have] `NBC.VENTILATION_HABITABLE` |
| V3 | Bath and kitchen ventilated (window, shaft or exhaust) | [have] `NBC.VENTILATION_BATHROOM/KITCHEN` |
| V4 | Prefer light from two sides | [have] `DESIGN.SINGLE_ASPECT` |
| V5 | Cross-draught inlet and outlet on different faces | [part] `SINGLE_ASPECT` proxies it |
| V6 | Wet rooms grouped on a shared plumbing line | [gap] `P.WET_GROUPING` cites only the parking rule |
| V7 | Wet rooms stacked floor to floor | [gap] |
| V8 | No toilet directly above a kitchen, dining or pooja | [gap] |
| V9 | Kitchen sink and bath drains on an exterior or shaft wall | [gap] |
| V10 | OHT above the wet stack, not above a bedroom | [gap] |
| V11 | Vastu zone per room type | [have] 9 weighted `VASTU.*` |
| V12 | Brahmasthan (plan centre) kept light | [have] `VASTU.BRAHMASTHAN` |
| V13 | Client's stated direction outranks the default table | [have] by design |

### Egress — NBC 2016 Part 4 (family entirely absent)

All **[gap]**. Proposed prefix `EGRESS.*`.

| # | Rule |
|---|---|
| X1 | `EGRESS.TRAVEL_DISTANCE` — furthest habitable point to a final exit within the occupancy limit |
| X2 | `EGRESS.DEAD_END` — dead-end corridor ≤ 3 m unsprinklered, ≤ 6 m sprinklered |
| X3 | `EGRESS.EXIT_DOOR_WIDTH` — designated exit doorway ≥ 1000 mm, opens in the escape direction |
| X4 | `EGRESS.STAIR_WIDTH` — ≥ 1000 mm for residential under 15 m (we check 900 mm today, which is the room-width rule, not the exit rule) |
| X5 | `EGRESS.SECOND_EXIT` — a second means of escape where the storey count requires it |
| X6 | `EGRESS.NO_LOCKED_ROUTE` — the escape route does not pass through a private room |

### Accessibility — Harmonised Guidelines 2021 (family entirely absent)

All **[gap]**. Proposed prefix `ACCESS.*`. Gate on a brief flag, since it changes
minima everywhere and should not fire on a plan nobody asked to be accessible.

| # | Rule |
|---|---|
| C1 | `ACCESS.DOOR_CLEAR_WIDTH` — ≥ 900 mm clear (800 mm absolute floor) at entrance and at least one bath |
| C2 | `ACCESS.TURNING_CIRCLE` — 1500 mm circle in the accessible bath, the lift lobby, and one bedroom |
| C3 | `ACCESS.THRESHOLD` — ≤ 12 mm at every door on the accessible route |
| C4 | `ACCESS.RAMP_SLOPE` — 1:12 maximum where a level change is on the route |
| C5 | `ACCESS.CORRIDOR_WIDTH` — ≥ 1050-1200 mm on the accessible route, above the 900 mm NBC floor |
| C6 | `ACCESS.ROUTE_CONTINUOUS` — entrance to one bedroom, one bath and the kitchen with no step |

### Dimensional and furnishability

| # | Rule | Status |
|---|---|---|
| D1-D9 | NBC minima per type: area, width, ceiling | [have] 12 `NBC.*` |
| D10 | Aspect ratio cap per type | [have] `bridge.RELAXED_MAX_ASPECT` |
| D11 | Bath proportion, absolute and vs its bedroom | [have] 3 `DESIGN.BATH_*` on measured p5/p95 |
| D12 | Total wet share of carpet | [have] `DESIGN.WET_AREA_EXCESSIVE` |
| D13 | 1800 mm clear wardrobe run per bedroom | [have] `DESIGN.NO_WARDROBE_WALL` |
| D14 | Kitchen work triangle legs 1200-2700, perimeter 4000-8000 | [have] in `design.py` |
| D15 | Continuous kitchen counter run | [gap] |
| D16 | Door swings do not collide with each other or with fixtures | [part] furnisher rejects poses; no rule reports it |
| D17 | Every room's required contents actually fit | [have] `standards.CONTENTS_FLOOR_M2` |
| D18 | Dining table + 900 mm pull-out clearance fits | [part] via furnisher |
| D19 | Parking bay 2500 x 5000 mm per car | [gap] |
| D20 | Stair: riser ≤ 190, tread ≥ 250, 2R+T, landing interval, width | [have] 5 `NBC.STAIR_*` |

### Site and bylaw

| # | Rule | Status |
|---|---|---|
| S1-S8 | Setbacks, coverage, FAR, road width, max floors, RWH, unit-vs-site | [have] 11 `BYLAW.*` |
| S9 | Sump and septic tank separation | [gap] |
| S10 | Septic tank in the rear setback, accessible for pumping | [gap] |
| S11 | Driveway continuous and 3000 mm from gate to parking | [gap] |
| S12 | Compound wall does not encroach the setback inward | [gap] |

---

## 5. Tally

| Family | Have | Partial | Gap |
|---|---|---|---|
| Existence | 4 | 3 | 3 |
| Justification (`PURPOSE.*`) | 0 | 0 | **20** |
| Access and privacy | 21 | 2 | 3 |
| Reach (`REACH.*`) | 0 | 0 | **10** |
| Envelope | 5 | 1 | 7 |
| Egress (`EGRESS.*`) | 0 | 0 | **6** |
| Accessibility (`ACCESS.*`) | 0 | 0 | **6** |
| Dimensional | 13 | 3 | 3 |
| Site | 11 | 0 | 4 |
| **Total** | **54** | **12** | **62** |

The 100 existing rule IDs collapse to ~54 distinct checks here because several
IDs are variants of one statement across room types. `RULES.md` is the flat
one-line-per-rule version of the same content, counted per ID rather than per
statement: **163 rules, 100 implemented, 63 gaps.**

Three things have to happen before the gaps can be filled, in this order:

1. **Split the collapsed types** (§2). Roughly half the `PURPOSE.*` rules are
   unstateable until `passage` stops resolving to `foyer`.
2. **Build the walkable graph.** All ten `REACH.*` rules and the tortuosity
   measure need it, and nothing else in the system provides it.
3. **Merge `typology.py` into `topology.py`.** Both run inside `validate()` from
   two tables that disagree; `score.py` already says topology supersedes
   typology, but nothing deleted it. Adding a rule today means choosing one of
   three homes (`typology.TYPOLOGIES`, `topology.SCENARIOS`,
   `principles.PRINCIPLES`) and the other two silently drift.

Three defects found while testing the JSON path (`scripts/eval_json.py`):

* **`BRIEF.ZONE_UNMET` can never fire.** It reads `ctx.brief["_vastu_findings"]`,
  which nothing in the codebase ever writes. A brief that explicitly states
  "pooja in the NE" and gets it in the SW produces only the generic `VASTU.POOJA`
  warning, never a brief-level failure — which is the exact distinction the
  rule's own comment says it exists to make.
* **Evaluation depends on the `_fpeval` sidecar, and the editor never writes
  it.** Room categories, opening kinds and the plot polygon all live in
  `project["_fpeval"]`. `grep -rn "_fpeval" vendor/openPlan3D/src/` returns
  nothing, so any room a user draws by hand has no category and reads back as
  `"indoor"` — which is not a `roomtypes` key, so `canonical()` returns
  `"unknown"` and every category-driven rule silently skips it. Measured: the
  same plan with the sidecar stripped drops from 8 room categories to 1, and
  from 18 findings across 7 families to 7 rule ids, of which only
  `BYLAW.SITE_UNSPECIFIED` and `VASTU.BRAHMASTHAN` are still meaningful.
  `GEO.NO_FRONT_DOOR` fires because front-door identity is in the sidecar too.
* **`rules.report()`** — documented as the loop's reward term — runs only
  GEO + NBC + BYLAW + VASTU and is called only from tests. `loop.py` calls
  `validate()` so it does see the design findings, but anything wired to
  `report()` in future would be blind to every rule in this document.

---

## 6. How a prompt becomes a checkable requirement

The brief is natural language and the rules read a dict, so the comparison is a
three-hop translation. Hop 2 did not exist.

```
prompt  --LLM-->  DesignSpec  --brief_from_spec-->  brief["requirements"]  --check_brief-->  BRIEF.* findings
        (llm.py)  (spec.py)    (bridge.py)          (a plain dict)          (rules.py)
```

**Hop 1 — prompt to `DesignSpec`.** `llm.extract_spec` with a strict JSON schema;
the model never emits a coordinate. Measured 100% schema conformance, 96.4%
field-correct. This is the only place a model is trusted, and it is trusted only
to *read*, never to place.

**Hop 2 — `DesignSpec` to `brief["requirements"]`.** This is the join, and it is
mechanical, not a model's job — every field it needs is already on the spec:

| brief key | comes from | what it makes checkable |
|---|---|---|
| `rooms` | count of `spec.rooms` by canonical category, `optional` excluded | `BRIEF.ROOM_MISSING` |
| `adjacent` | `spec.adjacency` where `relation == "direct_access"` | `BRIEF.ADJACENCY_UNMET` |
| `touching` | `spec.adjacency` where `relation == "adjacent"` | `BRIEF.NOT_TOUCHING` |
| `not_adjacent` | `spec.adjacency` where `kind == "prohibited"` | `BRIEF.FORBIDDEN_ADJACENCY` |
| `vastu_zones` | `RoomSpec.preferred_zone` + `spec.vastu.requirements` strings | `BRIEF.ZONE_UNMET` |
| `attached_bath` | count of `RoomSpec.attached_bath` | `TOPO.ATTACHED_BATH_SHORTFALL` |
| `rooms["foyer"]` | `spec.entrance.via_foyer` | `BRIEF.ROOM_MISSING` |
| `site_kind`, `plot_area_sqft`, `habitable_floors` | spec fields | scenario resolution, `BYLAW.*` |
| `unchecked_relations` | `spec.adjacency` with `relation` in `visual`/`same_floor` | `BRIEF.RELATION_UNCHECKED` |

Two decisions in that table are load-bearing:

* **`direct_access` and `adjacent` are different requirements.** Mapping both
  onto the door check rejects a correct plan: "pooja adjacent to the living" is
  satisfied by a shared wall, and a door from a living room into a shrine is not
  what was asked for. So `touching` tests a shared boundary and `adjacent` tests
  a door.
* **`visual` and `same_floor` are reported, not dropped.** A sightline test and
  a per-room storey field do not exist yet. `BRIEF.RELATION_UNCHECKED` says so
  at weight 0.1, because a requirement that vanishes from the report looks
  exactly like one that passed.

**Hop 3 — requirements to findings.** `check_brief`, six rules, unchanged in
shape.

The same spec also drives the *solver*, via `bridge.spec_adjacency_pairs`: a
stated adjacency is now an input to the layout as well as an assertion on the
result. Those are not redundant — a `required_adjacency` the solver traded away
comes back as a `BRIEF.ADJACENCY_UNMET` error naming what it cost, which is the
difference between a solver failure and a declared trade-off.

### What was broken before this

* **Nothing built `requirements` from a spec.** The only producer was
  `score.py`, from hand-written suite ground truth. So the brief family ran in
  the offline suite runner and nowhere else — the entire prompt-to-structure
  comparison existed only against truth a human had typed.
* **`service/app.py` passed `brief=None`** on `/generate`, `/validate` and
  `/render`. All six `BRIEF.*` rules, `attached_bath` and the stated typology
  were inert in the deployed service; the scenario was always inferred.
* **`bridge.spec_to_programme` never read `spec.adjacency`.** A prompt saying
  "a balcony off the master bedroom" produced a spec carrying that adjacency and
  a programme carrying nothing about it.

All three are fixed. `brief` is now a parameter on every service endpoint;
`brief_from_spec` and `spec_adjacency_pairs` live in `bridge.py`.

---

## 6. Sources

Codes and guidelines:

* NBC 2016 Part 3 (habitable-room minima, ventilation) and Part 4 (fire and
  life safety: travel distance 15-45 m by occupancy, dead end 3 m / 6 m,
  1000 mm exit doorway, 1000 mm residential stair under 15 m).
  <https://fireandsafetyequipments.com/wp-content/uploads/2018/09/NBC2016-Part-IV.pdf>,
  <https://infralens.in/knowledge/nbc-2016-travel-distance-exit-width>
* Harmonised Guidelines and Standards for Universal Accessibility in India 2021
  (900 mm clear door, 1500 mm turning circle, 1:12 ramp, ≤ 12 mm threshold).
  <https://ccpd.nic.in/harmonized-guidelines-for-standards-of-accessibility/>
* MoHUA Model Building Bye-Laws, Chapter 4 general building requirements.
  <https://mohua.gov.in/upload/uploadfiles/files/Chap-4.pdf>

Research:

* *Space Syntax-guided Post-training for Residential Floor Plan Generation*,
  arXiv 2602.22507. Integration Oracle: TD → MD → RA → integration;
  `public_score = public_max − other_max`; living-room dominance normalised to
  plan average; non-compensatory validity gates. Already the basis of
  `syntax.py`. <https://arxiv.org/abs/2602.22507>
* *Comprehensive and Dedicated Metrics for Evaluating AI-Generated Residential
  Floor Plans* (RFP-A), Buildings 15(10):1674. Room-count compliance, spatial
  connectivity, room location, geometric features; rule-based plus graph-based.
  Only HouseDiffusion and FloorplanDiffusion exceeded 90% accuracy of six models
  tested. <https://www.mdpi.com/2075-5309/15/10/1674>
* *Automated generation of circulations within a floorplan*, Shiksha, Anand,
  Shekhawat & Agrawal (BITS Pilani), AI EDAM. Plane-graph floorplans; phase 1
  spanning circulation (a corridor reaching every room via a circulation graph),
  phase 2 approximation algorithm minimising circulation area, phase 3
  customised public/private circulation. **This is the reference for the
  walkable-graph primitive in §4 Q4.**
  <https://www.cambridge.org/core/journals/ai-edam/article/automated-generation-of-circulations-within-a-floorplan/17830B95C3A0B8120CE87C6F8610AA66>
* *Quality assessment of residential layout designs generated by relational
  GANs*, Automation in Construction.
  <https://www.sciencedirect.com/science/article/abs/pii/S0926580523005034>
* ResPlan, arXiv 2508.14006 — the 17,000-plan corpus already converted.

Indian practice (conventions, not code — every one is a default a client can
override):

* Pooja room: north-east, idol faces west, never under a stair or beside a
  toilet, enclosed room from ~1500 x 2100 mm.
  <https://www.studiomatrx.org/guides/pooja-room-design-india>,
  <https://happho.com/how-to-design-a-pooja-room-in-your-home/>
* Room placement by direction: entrance N/E/NE, master SW, kitchen SE, pooja NE,
  no toilet NE, stair S/W/SW, living N/E.
  <https://www.bajajfinserv.in/house-plan-as-per-vastu>
* Servant room: west side preferred, adjacent to kitchen and utility because
  that is where the work is; not north, east or south-west.
  <https://www.livevaastu.com/vastu-tips-servent-rooms.html>,
  <https://www.subhavaastu.com/vastu-tips-servent-rooms.html>
* Standard Indian room sizes.
  <https://www.houseyog.com/blog/minimum-room-size-standards-india/>,
  <https://civilsir.com/minimum-standard-size-of-room-in-residential-building/>
* Space-planning efficiency ratios and circulation factor.
  <https://zensets.com/calculators/space-planning-calculator>
* Neufert, *Architects' Data* — clearances, furniture-driven room minima, and the
  canonical residential adjacencies (kitchen-dining, mudroom between garage and
  living, bath attached to primary bedroom).
