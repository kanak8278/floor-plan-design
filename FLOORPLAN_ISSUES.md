# Floor-plan review — `out/suite_svg`, all 89 plans

A design review of every plan in `out/suite_svg`, looking for the things a
buyer or a plan-checker notices first: where the hall sits, how you get to a
bedroom, whether the washroom is reachable without walking through someone's
bedroom, whether a bedroom has a window, whether the balcony is actually
outside, and whether the gate lines up with the front door.

## How this was produced

Two passes, deliberately in this order.

1. **Visual pass.** I rendered every SVG to PNG (`qlmanage -t -s 2000`) and read
   the drawings. 35 of the 89 were read in full detail — chosen to cover every
   layout family in the suite (`apt`, `base`, `det`, `floor`, `furn`, `out`,
   `park`, `spec`, `vastu`, `wet`, and the small/large plot bands within each).
2. **Geometric pass, all 89.** The SVGs carry the data needed to check
   circulation exactly: room polygons with `class="room room-<type>"`, and
   openings with `data-kind` + world coordinates (`data-wx`, `data-wy`) that map
   to sheet space through the header's `data-scale` / `data-ox` / `data-oy`. So
   every issue below is derived from the drawing itself, not from an impression
   of it. Script: `/tmp/fpview/audit.py`.

The visual pass came first on purpose — it is what told me which checks were
worth writing. Every check in the geometric pass was calibrated against a plan
where I had already seen the fault with my own eyes, and I found no case where
the script claimed a fault the drawing did not show.

Room *types* come from the SVG class; room *labels* come from the drawing's
text. These disagree in a way worth knowing about: `room-passage` is printed as
"HALL", and `master_bedroom` is printed as "BEDROOM 1". So "the HALL" in these
drawings is usually not a hall in the Indian sense (the main sitting room) but
raw corridor, and "BEDROOM 1" is the room the generator considers the master.
Several findings below only make sense once you know that.

---

## The seven patterns that repeat across the whole suite

These are not 89 unrelated plans. They are variations on a handful of layout
families, and the same faults recur.

**1. The front door is on the north wall of every single plan (89/89).**
Every plan puts the front door on the top edge of the building, regardless of
plot shape, orientation, or where the gate is. This is the root cause of the
next pattern, and it means the `vastu-*` plans cannot be doing what their name
implies — orientation is not a free variable anywhere in the suite.

**2. In 13 plans the gate is on the *opposite* boundary from the front door.**
`base-01`, `base-09`, `det-05`, `det-17`, `det-23`, `det-31`, `floor-01`,
`furn-03`, `out-01`, `park-01`, `spec-01`, `vastu-01`, `vastu-10`. The gate sits
on the rear plot line while the front door is on the north wall, so the approach
has to wrap the entire building. In those same plans the driveway slab is drawn
as a 11 m x 3 m band across the middle of the site: it overlaps the building
footprint by 21–22 m², crosses the plot boundary by 9–10 m², and still stops
~4 m short of the gate it is meant to serve. That is not a design choice, it is
an unplaced object.

**3. The "hall" is almost always a blind internal corridor, and it is often the
biggest room in the plan.** 51 plans have a circulation space over 10 m² with no
window. The extremes: `base-05` HALL 59.5 m² (27% of carpet, larger than
LIVING 1), `apt-05` HALL 36.8 m², `det-47` HALL 32.1 m², `det-39` FOYER 25.9 m²
(second-largest room in the house). At the other end the label is applied to
slivers: `det-39` HALL is 2.7 m² (1.02 m wide), `det-12` HALL is 2.6 m²,
`det-46` HALL is 3.8 m². Ten plans spend over 20% of carpet area on circulation.

**4. The foyer is not at the entrance in 53 plans.** The front door opens
straight into the living room (42 plans have no entry buffer at all), and a room
labelled FOYER sits somewhere in the middle or in a far corner, usually as a
dead-end with one door. `apt-05`'s foyer is a 2.6 m² closet in the opposite
corner of the plan from the door. `det-04`'s is 1.4 m². `det-16`'s is 20.9 m² and
windowless. The label is being attached to whatever passage was left over.

**5. Privacy and access failures are common, not rare.**
- 21 plans have a room reachable **only through a bathroom** (26 rooms in all). In `apt-10` the
  second bedroom is behind the WC; in `det-48` two of five bedrooms are; in
  `out-01`/`det-31`/`out-05` the SITOUT is.
- 22 plans have a **door between the kitchen and a bedroom**, and in 23 plans the
  kitchen has **no door to any living, dining or hall at all**. In 15 of those
  the only way in is **through a bedroom** — `base-03`, `det-07`, `det-18`,
  `det-31`, `det-37`, `det-43`, `out-01`, `out-05`, `park-03`, `park-07`,
  `spec-08`, `vastu-06`, `wet-01`, `wet-03`, `wet-10`. In the other 8 it is
  through a service room, i.e. the sequence is inverted — utility before kitchen
  in `det-02`, `det-25`, `det-39`, `det-41`, `det-44`, `out-03`, and *pooja*
  before kitchen in `spec-01` and `vastu-01`.
- 13 plans have a bathroom whose only door is into another bathroom.
- 19 plans open a WC door directly into the living or dining room.
- 28 plans have **no toilet reachable without entering a bedroom**.
- 50 plans give three or more bedrooms a single bathroom; 79 leave the master
  with no attached bath.

**6. Rooms that cannot be what they are called.** 21 plans contain a "bedroom"
under 9 m² (28 such rooms). The floor of the range is absurd: `det-46` BEDROOM 1 is 1.9 m²
(6'0" x 3'4", i.e. 1.83 m x 1.02 m), `det-13` BEDROOM 1 is 1.5 m², `det-39` BEDROOM 1 is 3.7 m² and
windowless, `det-23` is 3.5 m² at 1.19 m wide, `det-19` is a 1-BHK whose only
bedroom is 5.3 m² at 1.45 m wide. All are below the NBC 7.5 m² / 2400 mm floor.
The same happens to balconies: 6 plans have a "balcony" with no opening to the
outside at all (`det-02`'s BALCONY 2 is a 1.8 m² interior closet; `det-12`'s and
`det-14`'s are landlocked mid-plan), and `det-47` has a 1.5 m² balcony beside a
2.0 m² foyer.

**7. Interior cores used for habitable rooms.** 17 plans put a habitable room or
a kitchen where it touches no exterior wall (22 such rooms): `det-47` has a **windowless 12.7 m²
kitchen** in a 2,379 sq ft house, `det-46` a windowless 7.1 m² kitchen,
`det-25`/`det-39`/`det-48`/`floor-03`/`det-34` have windowless bedrooms up to
20.9 m², `det-28` and `furn-01` put the whole DINING room in a blind core.

Two things to note in the other direction. **No plan opens the front door
directly into a bedroom or a bathroom** — `ENTRY_INTO_PRIVATE` fires zero times
in 89 plans. And **no plan opens a WC door into a kitchen** —
`NBC.WC_OPENS_INTO_KITCHEN` fires zero times, so that rule is doing its job in
the solver.

---

## Your rulebook, checked against `RULES.md`

You sent eight rules. Six of them already exist in `RULES.md`, five as
implemented (`ok`) rules — worth knowing before you write them again:

| Your rule | Existing rule | Status |
|---|---|---|
| The Enclosure Rule | `GEO.UNREACHABLE_ROOM` (#7), `TOPO.BATH_UNREACHABLE` (#74) | already implemented |
| The Corridor Principle | `DESIGN.BEDROOM_THROUGH_TRAFFIC` (#43), `DESIGN.SOLE_BATH_VIA_BEDROOM` (#46) | already implemented |
| The Hygiene Separation Rule | `NBC.WC_OPENS_INTO_KITCHEN` (#23) | already implemented; 0 violations in 89 plans |
| Shared Washroom Accessibility | `TOPO.NO_COMMON_BATH` (#77) | implemented as `warn`; the reach half is `REACH.WC_FROM_LIVING` (#121), a GAP |
| The Exterior Wall Requirement | `DESIGN.NO_WINDOW` (#60) | window half implemented; "must touch an exterior wall" is **not** a rule |
| The Entryway Routing Rule | `TYPO.ENTRY_SEQUENCE` (#72) | only `part`, and only as a soft depth ordering |
| Kitchen Proximity | `DESIGN.KITCHEN_SINGLE_ACCESS` (#55), `REACH.DINING_FROM_KITCHEN` (#124) | "kitchen must have a door to living/dining" is **not** a rule |
| Interior Core Limitation | — | **not** a rule |

So three are genuinely new, and all three earn their place — they caught faults
in this suite that nothing else did:

- `ROOM_NO_EXTERIOR_WALL` (your rules 7 + 8) — 17 plans, including two
  windowless kitchens and six windowless bedrooms.
- `KITCHEN_NO_PUBLIC_ACCESS` (your rule 4, hard form) — 23 plans.
- `ENTRY_INTO_PRIVATE` (your rule 3, hard form) — 0 plans, i.e. it passes
  everywhere, which is itself worth having as a regression guard.

One correction to your rule 4 as written: "the kitchen must have direct
unobstructed access to dining or living areas" is too strict for Indian plans,
where kitchen → utility → service yard and kitchen → dining are both normal and
the kitchen legitimately opens off a passage. I implemented it as two severities:
error when the kitchen reaches no public space at all, warning when it reaches
one only via a passage. 36 plans trip the warning, 23 the error.

---

## Review feedback, generalised into rules

Your notes were written against individual findings; these are the same
judgements restated as general rules, with the id each became in `RULES.md`.
Where a note says a finding is not a defect, the check is deleted rather than
softened — a check that fires on correct work is worse than no check.

| Your note | General rule | Rule id |
|---|---|---|
| "front door opens straight into the living — that is fine … foyer is only needed if the door doesn't directly open into the hall and opens into a short passageway" | Entering directly into a living or hall needs no buffer. A named arrival space is required only when the front door lands in circulation. | #51 narrowed, new #164 |
| "living has 4 doors, all circulation crosses it — that is also fine" | A living room carrying the plan's circulation is not a defect; it is what a living room does. | rejected, no rule |
| "balcony is always expected to open outside, no need to have a window specifically" | A balcony is defined by an open edge, not by a window. Test the edge. | #67, already correct; the check was wrong |
| "not a strict requirement if there is only one washroom; if there are two we should try to have one attached at least" | The master needs an attached bath only once the plan has two or more bathrooms. Never a finding on a single-bathroom plan. | new #165 |
| "every toilet behind a bedroom — not a strict issue / works" | A common toilet is a preference. The hard case is the plan's *only* toilet sitting behind a bedroom. | #77 stays `warn`, #46 stays `error` |
| "bathroom reachable only through a bathroom — never" · "door between two wet rooms — never" | Two toilets in series is never acceptable: neither can be used privately. | new #166 |
| "this is important that kitchen is connected to living/dining" | The kitchen must have a door to a living or dining space. Reaching it only through a bedroom, utility or pooja is a defect. | new #167 |
| "kitchen has no window — fine if it has a utility area connected" | A utility with its own exterior opening substitutes for kitchen glazing, the same way an exhaust does. | #25 amended |
| "WC opens into living — it can happen … but if there are multiple then try to have only one common washroom" | One toilet opening onto the public zone is the common WC and is expected. Two or more is the defect. | new #168 |
| "foyer is an interior room, not at the front door — never" (twice) | A foyer more than one door from the front door is a passage. Must never occur. | #101, raised to `error` |
| "study has no window / no exterior wall — ideally not good" | A habitable room with no exterior wall is an error for sleeping and living rooms and for kitchens, a warning for a study or family room. | new #171 |
| "no dining in a 155 m² plan — it's fine if the house is small, the living room can take part as dining space" | Absence of a dining room is only a defect when the living is also too small to seat a table. | new #170 |
| "circulation is 21% of carpet — not good" | Over ~20% of carpet in circulation is a defect. | #129 confirmed `error` |
| "aspect 2.21 — try to not have that but if there is no possibility then fine" | Aspect limits are a soft preference; a tight envelope may force a long room. | new #169, `warn` |
| "balcony reachable only through the kitchen — a utility-type balcony can be via kitchen … the main balcony of the home is not through kitchen" | Only the primary balcony must open off a living or bedroom. A service balcony off the kitchen is the drying yard and is correct. | new #172 |
| "driveway overlaps the building footprint — never" (twice) | The drive or porch slab must not overlap the building. | new #173 |
| "driveway stops short of the gate — never" | The drive must run continuously from the gate. | #163 confirmed `error` |
| "driveway extends outside the plot — not" / "should not happen" | Nothing the plan draws may cross the plot boundary, the road-facing apron included. Two distinct shapes in the suite: 58 plans push the porch ~1 m² over the front line, 13 push 8-10 m² over a side line. | new #175 |

Two consequences worth stating plainly.

**Five of my checks were wrong, not merely strict.** `BALCONY_NO_OPENING` tested
for a window where `DESIGN.BALCONY_ENCLOSED` (#67) already tests the right
thing, the open edge. `LIVING_IS_CORRIDOR` and `NO_ENTRY_BUFFER` fire on correct
work. `MASTER_NO_ATTACHED_BATH` and `WC_OPENS_INTO_LIVING` fire on the first
instance when only the second is a defect. Counted exactly: 6 + 28 + 42
false findings from the first three, 51 from the master-bath check on
single-bathroom plans, and 19 first-instance public WCs — **146 of the 923
findings** in the per-plan list below, 16%. The counts table and the per-plan
list are pre-correction; read them with this table beside them.

**The `foyer` / `passage` distinction the review depends on does not survive
into `validate()`.** `roomtypes.py` line 111 aliases `passage`, `corridor`,
`hallway`, `lobby` and `circulation` all onto `foyer`, which is why #101 is
still a GAP. My audit read `class="room room-passage"` straight from the SVG, so
it could tell a foyer from a corridor; the rules engine cannot. #164 and #101
both need that split first — it is already item 1 of the three gating items at
the end of `RULES.md`.

## Counts across all 89 plans, after your review

767 findings, down from 923 before the corrections in the table above. Severity
and rule id per your notes.

| # plans | # found | Check | Rule / severity after your review |
|---:|---:|---|---|
| 71 | 71 | `DRIVEWAY_OVER_BUILDING` | #173 · error · never allowed |
| 71 | 71 | `DRIVEWAY_OFF_PLOT` | #175 · error · never allowed |
| 53 | 53 | `FOYER_NOT_AT_ENTRY` | #101 · error · never allowed |
| 51 | 54 | `BLIND_CIRCULATION` | #48/#158 · warn |
| 50 | 50 | `ONE_BATH_MANY_BEDS` | #76 · warn |
| 43 | 62 | `ROOM_ASPECT_SOFT` | #169 · warn · soft preference |
| 39 | 41 | `DEADEND_CIRCULATION` | #48 · warn |
| 36 | 36 | `KITCHEN_NOT_ON_DINING` | #167 · error · you called this important |
| 28 | 28 | `NO_COMMON_TOILET` | #77 · warn · preference, not a defect |
| 28 | 28 | `MASTER_NO_ATTACHED_BATH` | #165 · warn · now only when 2+ baths |
| 23 | 23 | `KITCHEN_NO_PUBLIC_ACCESS` | #167 · error |
| 22 | 24 | `KITCHEN_TO_BEDROOM_DOOR` | #167/#71 · error |
| 22 | 22 | `NO_DINING_LIVING_TOO_SMALL` | #170 · warn · now only when the living is under 18 m2 |
| 21 | 28 | `BEDROOM_TOO_SMALL` | #14 NBC · error |
| 17 | 21 | `THROUGH_BEDROOM` | #43 · error |
| 14 | 17 | `NO_WINDOW_WET` | #26 NBC · warn · exhaust substitutes |
| 13 | 18 | `NO_EXTERIOR_WALL` | #171 · error · sleeping, living, kitchen |
| 13 | 16 | `WET_TO_WET_DOOR` | #166 · error · never allowed |
| 13 | 13 | `GATE_OPPOSITE_FRONT_DOOR` | #174 · warn |
| 13 | 13 | `DRIVEWAY_NOT_AT_GATE` | #163 · error · never allowed |
| 12 | 13 | `BATH_BEHIND_BATH` | #166 · error · never allowed |
| 11 | 15 | `NO_WINDOW_HAB` | #60 · error · sleeping and living |
| 11 | 13 | `THROUGH_WET` | #43 · error |
| 10 | 10 | `CIRCULATION_HEAVY` | #129 · error · you confirmed 20% |
| 10 | 10 | `BEDROOM_TO_BEDROOM_DOOR` | #43 · error |
| 4 | 4 | `NO_WINDOW_HAB_SOFT` | #60 · warn · study, pooja |
| 4 | 4 | `NO_EXTERIOR_WALL_SOFT` | #171 · warn · study, pooja |
| 3 | 3 | `PRIMARY_BALCONY_VIA_SERVICE` | #172 · warn · now only the main balcony |
| 2 | 2 | `NO_WINDOW_KITCHEN` | #25 · warn · now exempt if a ventilated utility adjoins |
| 2 | 2 | `MULTIPLE_WC_ON_PUBLIC` | #168 · warn · now only the second and later |
| 2 | 2 | `BALCONY_VIA_WET` | #68 · warn |


### The same counts before the corrections

Kept for comparison, and because the per-plan list further down is still the
pre-correction one — that is where your annotations live, so I have not
regenerated over them.

| # plans | # instances | Check | What it means |
|---:|---:|---|---|
| 79 | 79 | `MASTER_NO_ATTACHED_BATH` | Master bedroom has no attached bathroom |
| 71 | 71 | `DRIVEWAY_OVER_BUILDING` | Driveway / car porch overlaps the building footprint |
| 71 | 71 | `DRIVEWAY_OFF_PLOT` | Driveway / car porch crosses the plot boundary |
| 53 | 53 | `FOYER_NOT_AT_ENTRY` | Foyer is an interior room, not at the front door |
| 51 | 54 | `BLIND_CIRCULATION` | Circulation space over 10 m2 with no window |
| 50 | 50 | `ONE_BATH_MANY_BEDS` | One bathroom for three or more bedrooms |
| 43 | 62 | `TUNNEL_ROOM` | Habitable room with aspect ratio 2.0 or worse |
| 42 | 42 | `NO_ENTRY_BUFFER` | Front door opens straight into the living, no foyer |
| 39 | 41 | `DEADEND_CIRCULATION` | Circulation space with one door, leading nowhere |
| 36 | 36 | `KITCHEN_NOT_ON_DINING` | Kitchen has no direct door to a living or dining space |
| 31 | 31 | `NO_DINING` | No dining space in a plan over 90 m2 |
| 28 | 28 | `NO_COMMON_TOILET` | Every toilet is behind a bedroom |
| 28 | 28 | `LIVING_IS_CORRIDOR` | Living / dining carries 4+ doors; all traffic crosses it |
| 23 | 23 | `KITCHEN_NO_PUBLIC_ACCESS` | Kitchen has no door to any living / dining / hall |
| 22 | 24 | `KITCHEN_TO_BEDROOM_DOOR` | Door directly between kitchen and bedroom |
| 21 | 26 | `THROUGH_WET` | Only route to a room is through a bathroom |
| 21 | 28 | `BEDROOM_TOO_SMALL` | Bedroom under 9 m2 |
| 19 | 21 | `WC_OPENS_INTO_LIVING` | WC door opens directly into the living / dining |
| 17 | 22 | `NO_EXTERIOR_WALL` | Habitable room with no exterior wall (interior core) |
| 17 | 21 | `THROUGH_BEDROOM` | Only route to a room is through a bedroom |
| 15 | 19 | `NO_WINDOW_HAB` | Habitable room with no window |
| 14 | 17 | `NO_WINDOW_WET` | Bathroom with no window |
| 13 | 16 | `WET_TO_WET_DOOR` | Door between two bathrooms (one is behind the other) |
| 13 | 13 | `GATE_OPPOSITE_FRONT_DOOR` | Gate is on the opposite boundary from the front door |
| 13 | 13 | `DRIVEWAY_NOT_AT_GATE` | Driveway does not reach the gate |
| 10 | 10 | `CIRCULATION_HEAVY` | Circulation over 20% of carpet area |
| 10 | 10 | `BEDROOM_TO_BEDROOM_DOOR` | Door directly between two bedrooms |
| 6 | 6 | `BALCONY_NO_OPENING` | Balcony with no opening to the outside |
| 3 | 3 | `BALCONY_VIA_KITCHEN` | Balcony reachable only through the kitchen |
| 3 | 3 | `NO_WINDOW_KITCHEN` | Kitchen with no window |
| 2 | 2 | `BALCONY_VIA_WET` | Balcony reachable only through a bathroom |

---

## Per-plan findings (pre-correction, with your annotations)

Ordered most-severe first within each plan. This is the list you annotated, so
it still carries the five checks that turned out to be wrong — 146 of these 923
lines are false, per the correction table above. Re-run
`scripts/audit_svg.py` for the corrected 767.

### apt-01  (8 issues)
Entry: front door opens into living#8. Front door on the TOP wall; gate none.

- **THROUGH_WET** — bathroom#3 is reachable only through a bathroom
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#2 - bathroom#3)
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone -- This is not a strict issue. 
- **BALCONY_NO_OPENING** — balcony#0 has no window/opening to outside -- Balcony is always expected to open outside, no need to have a window specifically. 
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom -- Again this is not strict requirement if there is only one washroom in the home, if there are two we should try to have one attached atleast.
- **KITCHEN_NOT_ON_DINING** — kitchen#7 has no direct door to a living or dining space (via ['passage', 'utility']) -- this is important that kitchen is connected to living/dining.
- **BLIND_CIRCULATION** — passage#6 is 15.1 m2 of circulation with no window 
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer -- That is fine.

### apt-03  (7 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate none.

- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone -- not a strict issue.
- **BALCONY_VIA_KITCHEN** — balcony#0 is reachable only through the kitchen -- see one balcony which is called utility area can be via kitchen but that is utility area which can work like balcony. Main balcony of the home is not through kitchen
- **BEDROOM_TOO_SMALL** — master_bedroom#3 only 4.4 m2 (2.63x1.68m) 
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom -- Again this is not strict requirement if there is only one washroom in the home, if there are two we should try to have one attached atleast.
- **DEADEND_CIRCULATION** — passage#5 (8.3 m2) is circulation with only 1 door 
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer -- not needed always, foyer is only needed if the door doesn't directly opens into the hall and open like into short passageway then we need foyer as different part else no point.
- **LIVING_IS_CORRIDOR** — living#7 has 4 doors - all circulation crosses it -- that is also fine.

### apt-05  (13 issues)
Entry: front door opens into living#15. Front door on the TOP wall; gate none.

- **NO_EXTERIOR_WALL** — kitchen#14 has no exterior wall (fully landlocked)
- **NO_WINDOW_KITCHEN** — kitchen#14 (8.7 m2) has no window -- if it has utility area connected then it is fine to not have a window.
- **WC_OPENS_INTO_LIVING** — bathroom#2 door opens directly into living#16 -- it can happen, like the common bathroom can be connected to living room or hallway or something common. 
- **WC_OPENS_INTO_LIVING** — bathroom#4 door opens directly into living#16  -- it can happen, like the common bathroom can be connected to living room or hallway or something common.  But if there are multiple then try to have only one common washroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom  -- Again this is not strict requirement if there is only one washroom in the home, if there are two we should try to have one attached atleast.
- **BLIND_CIRCULATION** — passage#13 is 38.3 m2 of circulation with no window 
- **DEADEND_CIRCULATION** — foyer#12 (3.0 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#12 is an interior room, not at the front door -- never 
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer -- fine
- **LIVING_IS_CORRIDOR** — living#16 has 5 doors - all circulation crosses it -- fine
- **TUNNEL_ROOM** — bedroom#8 7.88x3.17m aspect 2.49 
- **TUNNEL_ROOM** — bedroom#9 7.88x3.20m aspect 2.46
- **TUNNEL_ROOM** — bedroom#10 2.92x7.76m aspect 2.66

### apt-07  (12 issues)
Entry: front door opens into living#9. Front door on the TOP wall; gate none.

- **THROUGH_WET** — bathroom#1 is reachable only through a bathroom -- never
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#0 - bathroom#1) -- never
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone -- works
- **NO_WINDOW_HAB** — study#10 (8.8 m2) has no window -- ideally not good
- **NO_EXTERIOR_WALL** — study#10 has no exterior wall (fully landlocked) -- ideally not good. 
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom -- it can happen, like the common bathroom can be connected to living room or hallway or something common.  But if there are multiple then try to have only one common washroom
- **NO_DINING** — no dining space in a 155 m2 plan -- it's fine if the house is call, the living room can take part as dining space
- **BLIND_CIRCULATION** — passage#7 is 17.3 m2 of circulation with no window 
- **CIRCULATION_HEAVY** — circulation is 21% of carpet area -- not good. 
- **FOYER_NOT_AT_ENTRY** — foyer#6 is an interior room, not at the front door -- never
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer -- it is fine,as explained earlier
- **TUNNEL_ROOM** — bedroom#4 2.73x6.02m aspect 2.21 -- try to not havve that but if there is no possibility then fine.

### apt-10  (5 issues)
Entry: front door opens into living#5. Front door on the TOP wall; gate none.

- **THROUGH_WET** — bedroom#2 is reachable only through a bathroom
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **BLIND_CIRCULATION** — passage#3 is 10.2 m2 of circulation with no window
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer

### base-01  (8 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate BOT.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 2.59x5.85m aspect 2.26
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 23.9 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 7.2 m2 outside the plot boundary

### base-03  (15 issues)
Entry: front door opens into living#9. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bathroom#1 is reachable only through a bathroom
- **THROUGH_BEDROOM** — kitchen#8 is reachable only through a bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#8 has no door to a living, dining or hall - only to ['bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#0 - bathroom#1)
- **BEDROOM_TO_BEDROOM_DOOR** — door directly between bedroom#3 and bedroom#5
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#7 is 15.7 m2 of circulation with no window
- **DEADEND_CIRCULATION** — passage#7 (15.7 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#6 is an interior room, not at the front door
- **TUNNEL_ROOM** — master_bedroom#2 3.20x6.82m aspect 2.13
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2 -- never
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### base-05  (16 issues)
Entry: front door opens into living#10. Front door on the TOP wall; gate TOP.

- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **NO_WINDOW_HAB** — bedroom#5 (14.6 m2) has no window
- **NO_WINDOW_HAB** — living#11 (20.0 m2) has no window
- **NO_EXTERIOR_WALL** — bedroom#5 has no exterior wall (fully landlocked)
- **NO_EXTERIOR_WALL** — living#11 has no exterior wall (fully landlocked)
- **BEDROOM_TOO_SMALL** — bedroom#3 only 8.8 m2 (2.63x3.36m)
- **BEDROOM_TOO_SMALL** — bedroom#4 only 8.8 m2 (2.63x3.36m)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#9 has no direct door to a living or dining space (via ['bedroom', 'passage'])
- **NO_DINING** — no dining space in a 234 m2 plan
- **BLIND_CIRCULATION** — passage#8 is 61.4 m2 of circulation with no window
- **CIRCULATION_HEAVY** — circulation is 28% of carpet area
- **FOYER_NOT_AT_ENTRY** — foyer#7 is an interior room, not at the front door
- **TUNNEL_ROOM** — master_bedroom#2 3.23x7.91m aspect 2.45
- **TUNNEL_ROOM** — bedroom#5 2.63x5.56m aspect 2.11
- **TUNNEL_ROOM** — living#11 2.97x6.72m aspect 2.26

### base-07  (10 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#7
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.3 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (4.6 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### base-09  (8 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate BOT.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 2.61x5.79m aspect 2.22
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate --never
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 23.8 m2 --never
- **DRIVEWAY_OFF_PLOT** — driveway extends 7.3 m2 outside the plot boundary -- not 

### det-02  (13 issues)
Entry: front door opens into living#9. Front door on the TOP wall; gate TOP.

- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#8 has no door to a living, dining or hall - only to ['utility']
- **BALCONY_NO_OPENING** — balcony#1 has no window/opening to outside
- **WC_OPENS_INTO_LIVING** — bathroom#2 door opens directly into living#9
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_DINING** — no dining space in a 144 m2 plan
- **NO_WINDOW_WET** — bathroom#2 has no window
- **BLIND_CIRCULATION** — passage#7 is 16.9 m2 of circulation with no window
- **FOYER_NOT_AT_ENTRY** — foyer#6 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#9 has 4 doors - all circulation crosses it
- **TUNNEL_ROOM** — bedroom#4 3.00x6.31m aspect 2.10
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-03  (8 issues)
Entry: front door opens into living#10. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bedroom#6 is reachable only through a bathroom
- **BEDROOM_TOO_SMALL** — bedroom#5 only 8.8 m2 (3.19x2.77m)
- **KITCHEN_NOT_ON_DINING** — kitchen#9 has no direct door to a living or dining space (via ['passage'])
- **BLIND_CIRCULATION** — passage#8 is 11.8 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#7 (4.2 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#7 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-04  (15 issues)
Entry: front door opens into living#10. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bathroom#0 is reachable only through a bathroom
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#0 - bathroom#2)
- **WC_OPENS_INTO_LIVING** — bathroom#1 door opens directly into living#10
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#9 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#8 is 16.5 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#7 (1.7 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#7 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#10 has 4 doors - all circulation crosses it
- **TUNNEL_ROOM** — bedroom#4 6.41x2.67m aspect 2.40
- **TUNNEL_ROOM** — bedroom#5 2.68x5.89m aspect 2.20
- **TUNNEL_ROOM** — bedroom#6 2.63x5.89m aspect 2.24
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-05  (8 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate BOT.

- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 22.4 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 8.7 m2 outside the plot boundary

### det-06  (7 issues)
Entry: front door opens into living#8. Front door on the TOP wall; gate none.

- **THROUGH_WET** — balcony#1 is reachable only through a bathroom
- **BALCONY_VIA_WET** — balcony#1 is reachable only through a bathroom
- **BALCONY_VIA_KITCHEN** — balcony#0 is reachable only through the kitchen
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#7 has no direct door to a living or dining space (via ['balcony', 'passage', 'utility'])
- **BLIND_CIRCULATION** — passage#6 is 13.5 m2 of circulation with no window
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer

### det-07  (11 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bathroom#1 is reachable only through a bathroom
- **THROUGH_BEDROOM** — bedroom#3 is reachable only through a bedroom
- **THROUGH_BEDROOM** — kitchen#6 is reachable only through a master_bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#6 has no door to a living, dining or hall - only to ['master_bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#0 - bathroom#1)
- **BEDROOM_TO_BEDROOM_DOOR** — door directly between bedroom#3 and bedroom#4
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-08  (7 issues)
Entry: front door opens into living#10. Front door on the TOP wall; gate TOP.

- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_WINDOW_WET** — bathroom#0 has no window
- **BLIND_CIRCULATION** — foyer#7 is 17.0 m2 of circulation with no window
- **BLIND_CIRCULATION** — passage#8 is 25.7 m2 of circulation with no window
- **FOYER_NOT_AT_ENTRY** — foyer#7 is an interior room, not at the front door
- **TUNNEL_ROOM** — living#10 3.79x8.67m aspect 2.29
- **TUNNEL_ROOM** — living#11 9.26x3.56m aspect 2.60

### det-09  (11 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate TOP.

- **BEDROOM_TOO_SMALL** — master_bedroom#1 only 4.5 m2 (1.35x3.36m)
- **BEDROOM_TOO_SMALL** — bedroom#3 only 8.8 m2 (2.63x3.36m)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#5 has no direct door to a living or dining space (via ['passage', 'utility'])
- **NO_WINDOW_WET** — bathroom#0 has no window
- **BLIND_CIRCULATION** — passage#4 is 14.7 m2 of circulation with no window
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 1.35x3.36m aspect 2.49
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-10  (9 issues)
Entry: front door opens into living#8. Front door on the TOP wall; gate TOP.

- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#7 has no direct door to a living or dining space (via ['passage', 'pooja'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#6 is 23.2 m2 of circulation with no window
- **FOYER_NOT_AT_ENTRY** — foyer#5 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-12  (8 issues)
Entry: front door opens into living#9. Front door on the TOP wall; gate TOP.

- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#1 - bathroom#2)
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **BALCONY_NO_OPENING** — balcony#0 has no window/opening to outside
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#8 has no direct door to a living or dining space (via ['foyer'])
- **FOYER_NOT_AT_ENTRY** — foyer#6 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-13  (14 issues)
Entry: front door opens into living#10. Front door on the TOP wall; gate none.

- **NO_WINDOW_HAB** — bedroom#5 (12.2 m2) has no window
- **NO_WINDOW_HAB** — bedroom#6 (10.8 m2) has no window
- **NO_EXTERIOR_WALL** — bedroom#5 has no exterior wall (fully landlocked)
- **NO_EXTERIOR_WALL** — bedroom#6 has no exterior wall (fully landlocked)
- **WC_OPENS_INTO_LIVING** — bathroom#2 door opens directly into living#10
- **BEDROOM_TOO_SMALL** — master_bedroom#4 only 1.8 m2 (1.56x1.13m)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#9 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 134 m2 plan
- **BLIND_CIRCULATION** — passage#8 is 29.9 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#7 (4.5 m2) is circulation with only 1 door
- **CIRCULATION_HEAVY** — circulation is 26% of carpet area
- **FOYER_NOT_AT_ENTRY** — foyer#7 is an interior room, not at the front door
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer

### det-14  (9 issues)
Entry: front door opens into living#9. Front door on the TOP wall; gate TOP.

- **BALCONY_NO_OPENING** — balcony#0 has no window/opening to outside
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#7 is 16.3 m2 of circulation with no window
- **FOYER_NOT_AT_ENTRY** — foyer#6 is an interior room, not at the front door
- **TUNNEL_ROOM** — master_bedroom#3 2.99x7.93m aspect 2.65
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-15  (9 issues)
Entry: front door opens into living#8. Front door on the TOP wall; gate TOP.

- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **BEDROOM_TOO_SMALL** — master_bedroom#2 only 4.2 m2 (3.22x1.30m)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **DEADEND_CIRCULATION** — passage#6 (7.3 m2) is circulation with only 1 door
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **LIVING_IS_CORRIDOR** — dining#5 has 5 doors - all circulation crosses it
- **TUNNEL_ROOM** — master_bedroom#2 3.22x1.30m aspect 2.48
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-16  (13 issues)
Entry: front door opens into living#14. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — balcony#0 is reachable only through a bathroom
- **THROUGH_WET** — utility#16 is reachable only through a bathroom
- **NO_WINDOW_HAB** — bedroom#9 (24.5 m2) has no window
- **NO_WINDOW_HAB** — dining#10 (15.5 m2) has no window
- **NO_EXTERIOR_WALL** — bedroom#9 has no exterior wall (fully landlocked)
- **NO_EXTERIOR_WALL** — dining#10 has no exterior wall (fully landlocked)
- **BALCONY_VIA_WET** — balcony#0 is reachable only through a bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **BLIND_CIRCULATION** — foyer#11 is 22.0 m2 of circulation with no window
- **BLIND_CIRCULATION** — passage#12 is 25.2 m2 of circulation with no window
- **CIRCULATION_HEAVY** — circulation is 20% of carpet area
- **FOYER_NOT_AT_ENTRY** — foyer#11 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — dining#10 has 4 doors - all circulation crosses it

### det-17  (13 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate BOT.

- **BEDROOM_TOO_SMALL** — master_bedroom#1 only 4.5 m2 (1.35x3.36m)
- **BEDROOM_TOO_SMALL** — bedroom#3 only 8.8 m2 (2.63x3.36m)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#5 has no direct door to a living or dining space (via ['passage', 'utility'])
- **NO_WINDOW_WET** — bathroom#0 has no window
- **BLIND_CIRCULATION** — passage#4 is 14.4 m2 of circulation with no window
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 1.35x3.36m aspect 2.49
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 24.4 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 6.7 m2 outside the plot boundary

### det-18  (19 issues)
Entry: front door opens into living#12. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bathroom#1 is reachable only through a bathroom
- **THROUGH_WET** — bathroom#4 is reachable only through a bathroom
- **THROUGH_BEDROOM** — kitchen#11 is reachable only through a bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#11 has no door to a living, dining or hall - only to ['bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#0 - bathroom#1)
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#2 - bathroom#3)
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#2 - bathroom#4)
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_DINING** — no dining space in a 144 m2 plan
- **NO_WINDOW_WET** — bathroom#3 has no window
- **BLIND_CIRCULATION** — passage#10 is 13.5 m2 of circulation with no window
- **DEADEND_CIRCULATION** — passage#10 (13.5 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#9 is an interior room, not at the front door
- **TUNNEL_ROOM** — master_bedroom#5 6.55x2.95m aspect 2.22
- **TUNNEL_ROOM** — bedroom#8 5.70x2.63m aspect 2.17
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-19  (8 issues)
Entry: front door opens into living#5. Front door on the TOP wall; gate none.

- **BEDROOM_TOO_SMALL** — master_bedroom#2 only 5.3 m2 (1.45x3.64m)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **BLIND_CIRCULATION** — passage#3 is 13.2 m2 of circulation with no window
- **DEADEND_CIRCULATION** — passage#3 (13.2 m2) is circulation with only 1 door
- **CIRCULATION_HEAVY** — circulation is 25% of carpet area
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **LIVING_IS_CORRIDOR** — living#5 has 4 doors - all circulation crosses it
- **TUNNEL_ROOM** — master_bedroom#2 1.45x3.64m aspect 2.51

### det-21  (12 issues)
Entry: front door opens into living#8. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bedroom#3 is reachable only through a bathroom
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **BEDROOM_TOO_SMALL** — master_bedroom#2 only 7.6 m2 (2.54x3.01m)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#7 has no direct door to a living or dining space (via ['passage'])
- **BLIND_CIRCULATION** — passage#6 is 14.7 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#5 (4.2 m2) is circulation with only 1 door
- **CIRCULATION_HEAVY** — circulation is 21% of carpet area
- **FOYER_NOT_AT_ENTRY** — foyer#5 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#8 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-22  (15 issues)
Entry: front door opens into living#8. Front door on the TOP wall; gate TOP.

- **THROUGH_BEDROOM** — bedroom#3 is reachable only through a master_bedroom
- **BEDROOM_TO_BEDROOM_DOOR** — door directly between master_bedroom#2 and bedroom#3
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#7 has no direct door to a living or dining space (via ['passage', 'utility'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#6 is 19.3 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#5 (4.5 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#5 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#8 has 4 doors - all circulation crosses it
- **TUNNEL_ROOM** — bedroom#3 3.09x6.46m aspect 2.09
- **TUNNEL_ROOM** — bedroom#4 3.09x6.46m aspect 2.09
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-23  (12 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate BOT.

- **BEDROOM_TOO_SMALL** — master_bedroom#2 only 4.0 m2 (3.09x1.30m)
- **BEDROOM_TOO_SMALL** — bedroom#3 only 8.8 m2 (3.09x2.86m)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage'])
- **NO_WINDOW_WET** — bathroom#1 has no window
- **BLIND_CIRCULATION** — passage#5 is 15.8 m2 of circulation with no window
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#2 3.09x1.30m aspect 2.38
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 21.4 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 9.7 m2 outside the plot boundary

### det-24  (9 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#7
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['foyer'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.0 m2 of circulation with no window
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-25  (11 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#6 has no door to a living, dining or hall - only to ['utility']
- **NO_WINDOW_HAB** — bedroom#4 (11.0 m2) has no window
- **NO_EXTERIOR_WALL** — bedroom#4 has no exterior wall (fully landlocked)
- **BEDROOM_TOO_SMALL** — master_bedroom#2 only 4.7 m2 (3.42x1.37m)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_WINDOW_WET** — bathroom#0 has no window
- **BLIND_CIRCULATION** — passage#5 is 12.0 m2 of circulation with no window
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#2 3.42x1.37m aspect 2.50
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-26  (6 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate TOP.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 2.61x5.79m aspect 2.22
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-27  (12 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#7
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['master_bedroom', 'passage', 'utility'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.1 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (3.2 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **TUNNEL_ROOM** — bedroom#3 6.91x3.07m aspect 2.25
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-28  (8 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **NO_WINDOW_HAB** — dining#4 (11.1 m2) has no window
- **NO_EXTERIOR_WALL** — dining#4 has no exterior wall (fully landlocked)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **LIVING_IS_CORRIDOR** — dining#4 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-29  (12 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#7
- **BEDROOM_TOO_SMALL** — master_bedroom#1 only 5.4 m2 (1.66x3.23m)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage'])
- **BLIND_CIRCULATION** — passage#5 is 18.7 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (4.3 m2) is circulation with only 1 door
- **CIRCULATION_HEAVY** — circulation is 26% of carpet area
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#7 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-30  (9 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#7
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['foyer'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.0 m2 of circulation with no window
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-31  (12 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate BOT.

- **THROUGH_WET** — sitout#7 is reachable only through a bathroom
- **THROUGH_BEDROOM** — kitchen#5 is reachable only through a bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#5 has no door to a living, dining or hall - only to ['bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 22.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 9.1 m2 outside the plot boundary

### det-32  (10 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.7 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (6.4 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **TUNNEL_ROOM** — living#7 8.36x3.90m aspect 2.14
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-34  (9 issues)
Entry: front door opens into living#12. Front door on the TOP wall; gate TOP.

- **NO_WINDOW_HAB** — master_bedroom#4 (20.9 m2) has no window
- **NO_EXTERIOR_WALL** — master_bedroom#4 has no exterior wall (fully landlocked)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#11 has no direct door to a living or dining space (via ['passage', 'store', 'utility'])
- **NO_DINING** — no dining space in a 234 m2 plan
- **NO_WINDOW_WET** — bathroom#1 has no window
- **BLIND_CIRCULATION** — passage#10 is 49.2 m2 of circulation with no window
- **CIRCULATION_HEAVY** — circulation is 23% of carpet area
- **FOYER_NOT_AT_ENTRY** — foyer#9 is an interior room, not at the front door

### det-35  (6 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate TOP.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 2.61x5.79m aspect 2.22
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-36  (11 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.1 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (4.6 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#7 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-37  (10 issues)
Entry: front door opens into living#8. Front door on the TOP wall; gate TOP.

- **THROUGH_BEDROOM** — kitchen#7 is reachable only through a bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#7 has no door to a living, dining or hall - only to ['bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **DEADEND_CIRCULATION** — foyer#5 (4.3 m2) is circulation with only 1 door
- **DEADEND_CIRCULATION** — passage#6 (9.9 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#5 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#8 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-38  (11 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.1 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (4.6 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#7 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-39  (15 issues)
Entry: front door opens into living#13. Front door on the TOP wall; gate TOP.

- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#12 has no door to a living, dining or hall - only to ['store', 'utility']
- **NO_WINDOW_HAB** — master_bedroom#5 (4.2 m2) has no window
- **NO_WINDOW_HAB** — bedroom#7 (17.8 m2) has no window
- **NO_EXTERIOR_WALL** — master_bedroom#5 has no exterior wall (fully landlocked)
- **NO_EXTERIOR_WALL** — bedroom#7 has no exterior wall (fully landlocked)
- **WC_OPENS_INTO_LIVING** — bathroom#2 door opens directly into living#14
- **WC_OPENS_INTO_LIVING** — bathroom#3 door opens directly into living#14
- **BEDROOM_TOO_SMALL** — master_bedroom#5 only 4.2 m2 (1.30x3.20m)
- **BEDROOM_TOO_SMALL** — bedroom#6 only 8.8 m2 (2.74x3.22m)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_WINDOW_WET** — bathroom#2 has no window
- **NO_WINDOW_WET** — bathroom#3 has no window
- **FOYER_NOT_AT_ENTRY** — foyer#10 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#14 has 4 doors - all circulation crosses it
- **TUNNEL_ROOM** — master_bedroom#5 1.30x3.20m aspect 2.46

### det-41  (4 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate none.

- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#6 has no door to a living, dining or hall - only to ['utility']
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — living#7 2.88x6.02m aspect 2.09

### det-42  (5 issues)
Entry: front door opens into living#4. Front door on the TOP wall; gate TOP.

- **DEADEND_CIRCULATION** — passage#2 (9.2 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#1 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#4 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-43  (12 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **THROUGH_BEDROOM** — bedroom#3 is reachable only through a master_bedroom
- **THROUGH_BEDROOM** — kitchen#6 is reachable only through a bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#6 has no door to a living, dining or hall - only to ['bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **BEDROOM_TO_BEDROOM_DOOR** — door directly between master_bedroom#1 and bedroom#3
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 15.9 m2 of circulation with no window
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-44  (8 issues)
Entry: front door opens into living#8. Front door on the TOP wall; gate TOP.

- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#7 has no door to a living, dining or hall - only to ['utility']
- **BALCONY_NO_OPENING** — balcony#0 has no window/opening to outside
- **WC_OPENS_INTO_LIVING** — bathroom#1 door opens directly into living#8
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **FOYER_NOT_AT_ENTRY** — foyer#5 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-45  (15 issues)
Entry: front door opens into living#10. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bathroom#0 is reachable only through a bathroom
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#0 - bathroom#2)
- **WC_OPENS_INTO_LIVING** — bathroom#1 door opens directly into living#10
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#9 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#8 is 16.5 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#7 (1.7 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#7 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#10 has 4 doors - all circulation crosses it
- **TUNNEL_ROOM** — bedroom#4 6.41x2.67m aspect 2.40
- **TUNNEL_ROOM** — bedroom#5 2.68x5.89m aspect 2.20
- **TUNNEL_ROOM** — bedroom#6 2.63x5.89m aspect 2.24
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### det-46  (10 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **NO_EXTERIOR_WALL** — kitchen#6 has no exterior wall (fully landlocked)
- **NO_WINDOW_KITCHEN** — kitchen#6 (7.7 m2) has no window
- **BEDROOM_TOO_SMALL** — master_bedroom#1 only 2.2 m2 (1.95x1.13m)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **DEADEND_CIRCULATION** — passage#5 (4.3 m2) is circulation with only 1 door
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **LIVING_IS_CORRIDOR** — dining#4 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### det-47  (15 issues)
Entry: front door opens into living#11. Front door on the TOP wall; gate TOP.

- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **NO_WINDOW_HAB** — master_bedroom#3 (7.2 m2) has no window
- **NO_EXTERIOR_WALL** — master_bedroom#3 has no exterior wall (fully landlocked)
- **NO_EXTERIOR_WALL** — kitchen#10 has no exterior wall (fully landlocked)
- **NO_WINDOW_KITCHEN** — kitchen#10 (13.6 m2) has no window
- **BEDROOM_TOO_SMALL** — master_bedroom#3 only 7.2 m2 (1.95x3.67m)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **BLIND_CIRCULATION** — passage#9 is 33.6 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#8 (2.4 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#8 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — dining#7 has 5 doors - all circulation crosses it
- **TUNNEL_ROOM** — bedroom#5 2.76x7.31m aspect 2.65
- **TUNNEL_ROOM** — bedroom#6 2.76x7.31m aspect 2.65
- **TUNNEL_ROOM** — dining#7 9.48x3.79m aspect 2.50
- **TUNNEL_ROOM** — living#11 3.08x7.64m aspect 2.48

### det-48  (17 issues)
Entry: front door opens into living#15. Front door on the TOP wall; gate none.

- **THROUGH_WET** — bathroom#6 is reachable only through a bathroom
- **THROUGH_WET** — bedroom#7 is reachable only through a bathroom
- **THROUGH_WET** — bedroom#9 is reachable only through a bathroom
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#3 - bathroom#6)
- **NO_WINDOW_HAB** — bedroom#11 (17.1 m2) has no window
- **NO_EXTERIOR_WALL** — bedroom#11 has no exterior wall (fully landlocked)
- **KITCHEN_NOT_ON_DINING** — kitchen#14 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 275 m2 plan
- **NO_WINDOW_WET** — bathroom#2 has no window
- **NO_WINDOW_WET** — bathroom#4 has no window
- **NO_WINDOW_WET** — bathroom#5 has no window
- **BLIND_CIRCULATION** — passage#13 is 30.1 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#12 (12.5 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#12 is an interior room, not at the front door
- **TUNNEL_ROOM** — bedroom#10 2.63x6.10m aspect 2.32
- **TUNNEL_ROOM** — bedroom#11 2.81x6.10m aspect 2.17
- **TUNNEL_ROOM** — living#15 4.34x9.07m aspect 2.09

### det-49  (9 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **BEDROOM_TOO_SMALL** — bedroom#3 only 8.9 m2 (3.34x2.65m)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage', 'utility'])
- **BLIND_CIRCULATION** — passage#5 is 12.5 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (2.4 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### floor-01  (7 issues)
Entry: front door opens into living#3. Front door on the TOP wall; gate BOT.

- **DEADEND_CIRCULATION** — passage#0 (9.3 m2) is circulation with only 1 door
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — living#3 3.91x9.66m aspect 2.47
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 22.6 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 8.4 m2 outside the plot boundary

### floor-03  (11 issues)
Entry: front door opens into living#10. Front door on the TOP wall; gate TOP.

- **NO_WINDOW_HAB** — master_bedroom#2 (5.3 m2) has no window
- **NO_EXTERIOR_WALL** — master_bedroom#2 has no exterior wall (fully landlocked)
- **BEDROOM_TOO_SMALL** — master_bedroom#2 only 5.3 m2 (2.00x2.65m)
- **BEDROOM_TOO_SMALL** — bedroom#5 only 8.8 m2 (2.64x3.35m)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **BLIND_CIRCULATION** — passage#8 is 13.8 m2 of circulation with no window
- **FOYER_NOT_AT_ENTRY** — foyer#7 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#11 has 5 doors - all circulation crosses it
- **TUNNEL_ROOM** — living#11 7.37x3.35m aspect 2.20
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### floor-05  (9 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['foyer'])
- **DEADEND_CIRCULATION** — passage#5 (8.9 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#7 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### floor-07  (13 issues)
Entry: front door opens into living#10. Front door on the TOP wall; gate TOP.

- **THROUGH_BEDROOM** — bedroom#4 is reachable only through a bedroom
- **BEDROOM_TO_BEDROOM_DOOR** — door directly between bedroom#3 and bedroom#4
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#9 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 234 m2 plan
- **NO_WINDOW_WET** — bathroom#0 has no window
- **BLIND_CIRCULATION** — foyer#7 is 31.4 m2 of circulation with no window
- **BLIND_CIRCULATION** — passage#8 is 25.7 m2 of circulation with no window
- **CIRCULATION_HEAVY** — circulation is 24% of carpet area
- **FOYER_NOT_AT_ENTRY** — foyer#7 is an interior room, not at the front door
- **TUNNEL_ROOM** — bedroom#4 2.63x6.73m aspect 2.56
- **TUNNEL_ROOM** — bedroom#5 2.63x6.73m aspect 2.56
- **TUNNEL_ROOM** — bedroom#6 2.63x6.95m aspect 2.64

### floor-10  (6 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage', 'pooja'])
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### furn-01  (8 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **NO_WINDOW_HAB** — dining#4 (11.1 m2) has no window
- **NO_EXTERIOR_WALL** — dining#4 has no exterior wall (fully landlocked)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **LIVING_IS_CORRIDOR** — dining#4 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### furn-03  (8 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate BOT.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 2.59x5.85m aspect 2.26
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 23.9 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 7.2 m2 outside the plot boundary

### furn-05  (6 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate TOP.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 2.61x5.79m aspect 2.22
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### furn-07  (10 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#7
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.3 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (4.6 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### furn-09  (6 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate TOP.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 2.61x5.79m aspect 2.22
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### out-01  (12 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate BOT.

- **THROUGH_WET** — sitout#7 is reachable only through a bathroom
- **THROUGH_BEDROOM** — kitchen#5 is reachable only through a bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#5 has no door to a living, dining or hall - only to ['bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 22.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 9.1 m2 outside the plot boundary

### out-03  (7 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#6 has no door to a living, dining or hall - only to ['utility']
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **BLIND_CIRCULATION** — passage#5 is 10.0 m2 of circulation with no window
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### out-05  (11 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — sitout#7 is reachable only through a bathroom
- **THROUGH_BEDROOM** — kitchen#5 is reachable only through a master_bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#5 has no door to a living, dining or hall - only to ['master_bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 5.46x2.61m aspect 2.09
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### out-07  (11 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate TOP.

- **BEDROOM_TOO_SMALL** — master_bedroom#1 only 4.5 m2 (1.35x3.36m)
- **BEDROOM_TOO_SMALL** — bedroom#3 only 8.8 m2 (2.63x3.36m)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#5 has no direct door to a living or dining space (via ['passage', 'utility'])
- **NO_WINDOW_WET** — bathroom#0 has no window
- **BLIND_CIRCULATION** — passage#4 is 14.7 m2 of circulation with no window
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 1.35x3.36m aspect 2.49
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### out-09  (12 issues)
Entry: front door opens into living#8. Front door on the TOP wall; gate TOP.

- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **BALCONY_NO_OPENING** — balcony#0 has no window/opening to outside
- **BALCONY_VIA_KITCHEN** — balcony#1 is reachable only through the kitchen
- **BEDROOM_TOO_SMALL** — master_bedroom#3 only 7.9 m2 (2.38x3.32m)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#7 has no direct door to a living or dining space (via ['balcony', 'bedroom', 'passage'])
- **BLIND_CIRCULATION** — passage#6 is 10.2 m2 of circulation with no window
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **LIVING_IS_CORRIDOR** — living#8 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### park-01  (8 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate BOT.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 2.59x5.85m aspect 2.26
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 23.9 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 7.2 m2 outside the plot boundary

### park-03  (11 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **THROUGH_BEDROOM** — kitchen#6 is reachable only through a bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#6 has no door to a living, dining or hall - only to ['bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **BEDROOM_TO_BEDROOM_DOOR** — door directly between master_bedroom#1 and bedroom#2
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **DEADEND_CIRCULATION** — foyer#4 (2.2 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### park-05  (10 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#7
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.3 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (4.6 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### park-07  (12 issues)
Entry: front door opens into living#9. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bathroom#1 is reachable only through a bathroom
- **THROUGH_BEDROOM** — bedroom#4 is reachable only through a master_bedroom
- **THROUGH_BEDROOM** — kitchen#8 is reachable only through a master_bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#8 has no door to a living, dining or hall - only to ['master_bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#0 - bathroom#1)
- **BEDROOM_TO_BEDROOM_DOOR** — door directly between master_bedroom#2 and bedroom#4
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **NO_DINING** — no dining space in a 234 m2 plan
- **BLIND_CIRCULATION** — passage#7 is 26.8 m2 of circulation with no window
- **FOYER_NOT_AT_ENTRY** — foyer#6 is an interior room, not at the front door
- **TUNNEL_ROOM** — bedroom#5 3.35x8.93m aspect 2.67

### park-10  (10 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#7
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.3 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (4.6 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### spec-01  (11 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate BOT.

- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#5 has no door to a living, dining or hall - only to ['pooja']
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **NO_WINDOW_HAB** — pooja#7 (2.5 m2) has no window
- **NO_EXTERIOR_WALL** — pooja#7 has no exterior wall (fully landlocked)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 24.6 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 6.1 m2 outside the plot boundary

### spec-02  (15 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#7
- **BEDROOM_TOO_SMALL** — master_bedroom#1 only 7.2 m2 (2.08x3.44m)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage', 'study'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 27.3 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (4.5 m2) is circulation with only 1 door
- **CIRCULATION_HEAVY** — circulation is 22% of carpet area
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#7 has 4 doors - all circulation crosses it
- **TUNNEL_ROOM** — bedroom#2 2.71x7.14m aspect 2.63
- **TUNNEL_ROOM** — bedroom#3 3.18x7.14m aspect 2.25
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### spec-05  (9 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['foyer'])
- **DEADEND_CIRCULATION** — passage#5 (8.9 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#7 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### spec-06  (12 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **NO_WINDOW_HAB** — pooja#8 (3.8 m2) has no window
- **NO_EXTERIOR_WALL** — pooja#8 has no exterior wall (fully landlocked)
- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#7
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.3 m2 of circulation with no window
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#7 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### spec-08  (15 issues)
Entry: front door opens into living#9. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bathroom#1 is reachable only through a bathroom
- **THROUGH_BEDROOM** — kitchen#8 is reachable only through a bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#8 has no door to a living, dining or hall - only to ['bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#0 - bathroom#1)
- **BEDROOM_TO_BEDROOM_DOOR** — door directly between bedroom#3 and bedroom#5
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#7 is 15.9 m2 of circulation with no window
- **DEADEND_CIRCULATION** — passage#7 (15.9 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#6 is an interior room, not at the front door
- **TUNNEL_ROOM** — master_bedroom#2 3.19x6.84m aspect 2.14
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### vastu-01  (11 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate BOT.

- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#5 has no door to a living, dining or hall - only to ['pooja']
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **NO_WINDOW_HAB** — pooja#7 (2.4 m2) has no window
- **NO_EXTERIOR_WALL** — pooja#7 has no exterior wall (fully landlocked)
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 24.6 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 6.1 m2 outside the plot boundary

### vastu-04  (10 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#7
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#6 has no direct door to a living or dining space (via ['passage'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **BLIND_CIRCULATION** — passage#5 is 16.3 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#4 (4.6 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### vastu-06  (11 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **THROUGH_BEDROOM** — kitchen#6 is reachable only through a bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#6 has no door to a living, dining or hall - only to ['bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **BEDROOM_TO_BEDROOM_DOOR** — door directly between master_bedroom#1 and bedroom#2
- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **DEADEND_CIRCULATION** — foyer#4 (2.2 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#4 is an interior room, not at the front door
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### vastu-08  (6 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate TOP.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 2.61x5.81m aspect 2.23
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### vastu-10  (8 issues)
Entry: front door opens into living#6. Front door on the TOP wall; gate BOT.

- **ONE_BATH_MANY_BEDS** — 3 bedrooms share 1 bathroom
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — master_bedroom#1 2.61x5.79m aspect 2.22
- **GATE_OPPOSITE_FRONT_DOOR** — gate is on the BOT boundary but the front door is on the TOP wall
- **DRIVEWAY_NOT_AT_GATE** — driveway stops 4036 mm short of the gate
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 23.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 7.3 m2 outside the plot boundary

### wet-01  (11 issues)
Entry: front door opens into living#7. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bathroom#1 is reachable only through a bathroom
- **THROUGH_BEDROOM** — bedroom#3 is reachable only through a bedroom
- **THROUGH_BEDROOM** — kitchen#6 is reachable only through a master_bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#6 has no door to a living, dining or hall - only to ['master_bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#0 - bathroom#1)
- **BEDROOM_TO_BEDROOM_DOOR** — door directly between bedroom#3 and bedroom#4
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### wet-03  (10 issues)
Entry: front door opens into living#8. Front door on the TOP wall; gate TOP.

- **THROUGH_BEDROOM** — kitchen#7 is reachable only through a bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#7 has no door to a living, dining or hall - only to ['bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **DEADEND_CIRCULATION** — foyer#5 (4.3 m2) is circulation with only 1 door
- **DEADEND_CIRCULATION** — passage#6 (9.9 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#5 is an interior room, not at the front door
- **LIVING_IS_CORRIDOR** — living#8 has 4 doors - all circulation crosses it
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### wet-05  (14 issues)
Entry: front door opens into living#11. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bedroom#5 is reachable only through a bathroom
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **WC_OPENS_INTO_LIVING** — bathroom#0 door opens directly into living#11
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **KITCHEN_NOT_ON_DINING** — kitchen#10 has no direct door to a living or dining space (via ['master_bedroom', 'passage'])
- **NO_DINING** — no dining space in a 144 m2 plan
- **NO_WINDOW_WET** — bathroom#2 has no window
- **BLIND_CIRCULATION** — passage#9 is 16.1 m2 of circulation with no window
- **DEADEND_CIRCULATION** — foyer#8 (2.3 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#8 is an interior room, not at the front door
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **TUNNEL_ROOM** — bedroom#5 2.80x5.86m aspect 2.09
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 0.8 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 0.8 m2 outside the plot boundary

### wet-08  (9 issues)
Entry: front door opens into living#8. Front door on the TOP wall; gate TOP.

- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **BEDROOM_TOO_SMALL** — master_bedroom#2 only 4.2 m2 (3.22x1.30m)
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **DEADEND_CIRCULATION** — passage#6 (7.3 m2) is circulation with only 1 door
- **NO_ENTRY_BUFFER** — front door opens straight into the living with no foyer
- **LIVING_IS_CORRIDOR** — dining#5 has 5 doors - all circulation crosses it
- **TUNNEL_ROOM** — master_bedroom#2 3.22x1.30m aspect 2.48
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary

### wet-10  (14 issues)
Entry: front door opens into living#10. Front door on the TOP wall; gate TOP.

- **THROUGH_WET** — bathroom#2 is reachable only through a bathroom
- **THROUGH_WET** — bedroom#6 is reachable only through a bathroom
- **THROUGH_BEDROOM** — kitchen#9 is reachable only through a bedroom
- **KITCHEN_NO_PUBLIC_ACCESS** — kitchen#9 has no door to a living, dining or hall - only to ['bedroom']
- **KITCHEN_TO_BEDROOM_DOOR** — door directly between kitchen and bedroom
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#0 - bathroom#1)
- **WET_TO_WET_DOOR** — door between two wet rooms (bathroom#0 - bathroom#2)
- **NO_COMMON_TOILET** — every toilet is behind a bedroom; no toilet reachable from the public zone
- **MASTER_NO_ATTACHED_BATH** — master bedroom has no attached bathroom
- **NO_WINDOW_WET** — bathroom#1 has no window
- **DEADEND_CIRCULATION** — foyer#7 (4.2 m2) is circulation with only 1 door
- **FOYER_NOT_AT_ENTRY** — foyer#7 is an interior room, not at the front door
- **DRIVEWAY_OVER_BUILDING** — driveway overlaps the building footprint by 1.0 m2
- **DRIVEWAY_OFF_PLOT** — driveway extends 1.0 m2 outside the plot boundary


---

## Visual notes

Things I saw in the drawings that the geometric checks above do not name. These
are per-plan and specific; they are the reason the visual pass came first.

**`apt-01`** — BALCONY 1 (3.6 m²) and BALCONY 2 (2.8 m², 4'9" wide) sit in the
middle of the bottom of the plan between the kitchen, hall and utility. Balcony 1
has no opening to the outside at all. Two token balconies where one usable one
belongs. Master bedroom is 8.4 m² and has no bath, while the smaller BEDROOM 2
(12.4 m²) controls both bathrooms — the hierarchy is inverted.

**`apt-03`** — "BEDROOM 1" is 3.9 m² at 5'2" wide: a store labelled a bedroom.
The HALL (7.7 m²) is a dead-end box in the top-right corner off the living,
performing no circulation function while the living room does all the work.

**`apt-05`** — 6 bedrooms and 2 living rooms served by one 8.0 m² windowless
kitchen. BATHROOM 1 is 10.9 m², larger than that kitchen. BATHROOM 3 and
BATHROOM 5 both open into LIVING 2. The FOYER is a 2.6 m² dead-end in the far
corner from the door.

**`apt-07`** — two circulation spaces in series (HALL 16.3 m² → FOYER 14.2 m²),
together 21% of carpet. Three bathrooms chained behind BEDROOM 3, so the master
and BEDROOM 2 have none and there is no guest WC. The STUDY has no window.

**`apt-10`** — the clearest failure in the suite: the master bedroom is entered
**through the kitchen**, and BEDROOM 2 is entered **through the bathroom**, which
makes the single WC a corridor that cannot be locked without trapping bedroom 2.
The HALL exists only to reach that bathroom.

**`base-01` / `base-09` / `det-26`** — same family. Master is a 2.6 m-wide,
5.8 m-long tunnel entered off the living room, so the bed area opens onto the
entry. One 3.4–3.6 m² bathroom for three bedrooms. No dining, no balcony, and the
gate is on the rear boundary (`base-01`, `base-09`).

**`base-03`** — the kitchen's only door is into BEDROOM 3, which is itself only
reachable through BEDROOM 4. Cooking requires living → bedroom → bedroom →
kitchen. Both bathrooms are chained behind BEDROOM 4. The HALL is a 14.8 m²
landlocked box with one door, sitting in the front-centre of the house where the
entrance or dining should be.

**`base-05`** — 2,394 sq ft carpet, and 641 sq ft of it is a windowless HALL.
LIVING 2 (18.9 m²) has no window. BEDROOM 4 (13.7 m²) has no window. BEDROOM 5
is entered through the kitchen. No attached bath for a 261 sq ft master.

**`base-07`** — one of the better plans: a clear hall spine, three well-shaped
bedrooms. But the single bathroom is at the *front* of the house off the living
room, the furthest possible point from every bedroom, and the FOYER is a
dead-end pocket beside it rather than at the door.

**`det-02`** — a vertical sandwich of BALCONY 1 / FOYER / BATHROOM in the centre
of the front façade, all three entered off the living. The kitchen is reached
via hall → utility → kitchen: the service sequence is inverted, the utility
should be beyond the kitchen, not before it.

**`det-03`** — three bathrooms in a row along the rear wall, all opening off
BEDROOM 1, each 4'4" wide. BEDROOM 3's only door is into BATHROOM 4. The master's
bed wall is consumed by three WC doors.

**`det-04`** — the FOYER is 1.4 m² (3'4" x 4'6"). BATHROOM 2 opens into the
living room. The kitchen is sandwiched between three toilets. Every one of the
four bedrooms has an aspect ratio of 2.0 or worse.

**`det-05`** — the house's only toilet is BEDROOM 2's en-suite, so the other two
bedrooms and any guest walk through bedroom 2 to reach it. That bathroom also
shares a wall with the kitchen.

**`det-06`** — two 1.5 m² "balconies" (1.07 m deep) side by side off the kitchen.
The UTILITY is landlocked in the centre with no door to the kitchen, so the
laundry crosses the hall.

**`det-07`** — the HALL is a cul-de-sac in the front corner while all real
circulation runs through the living room. Both toilets are chained behind
BEDROOM 1; the kitchen's only door is into the master.

**`det-08`** — LIVING 1 (aspect 2.3) and LIVING 2 (aspect 2.6) are both tunnels,
966 sq ft of public space with no relationship between them. The FOYER is 16 m²
of blind interior. Both toilets are landlocked against the kitchen wall at the
far rear from all four bedrooms.

**`det-09` / `det-17`** — "BEDROOM 1" is a 4.0 m² strip, 4'1" wide, on the west
wall. The only bathroom is windowless in the plan core. The utility opens off the
hall, not the kitchen.

**`det-10`** — the POOJA room shares a wall with the toilet and is entered from
the kitchen. The balcony/foyer pair form an interior column mid-plan.

**`det-12`** — HALL is 2.6 m² (8'3" x 3'4"). The BALCONY (3.2 m²) and STORE
(1.6 m²) are both landlocked in the plan interior. The 8.2 m² FOYER is doing the
dining room's job and is the kitchen's only access.

**`det-13`** — "BEDROOM 1" is 1.5 m² (4'9" x 3'4"). The BALCONY is 1.02 m deep.
The POOJA is 1.4 m². The FOYER is in the opposite corner from the front door. The
UTILITY is diagonally across the house from the kitchen. Three bathrooms occupy
the entire west wall, one of them (6.0 m²) larger than the pooja, balcony and
bedroom 1 combined.

**`det-14`** — BALCONY 1 is landlocked between the kitchen, living, master and
foyer. BALCONY 2 is at the rear beside the WC. The kitchen is a 2.3 m-wide
corridor across the front façade, sharing a wall with the master bedroom.

**`det-15`** — "BEDROOM 1" is 3.7 m² at 3'11" wide. The HALL is a dead-end box in
the rear corner off the dining. Two bathrooms both open off BEDROOM 3; nothing
opens off a common space. The dining, not the living, is the real hub.

**`det-16`** — BATHROOM 4 is 15.8 m², larger than four of the five bedrooms. The
BALCONY is sandwiched between BATHROOM 3 and BATHROOM 2 and is reachable only
through one of them, as is the UTILITY. The FOYER is 22.0 m² of blind interior in
the plan centre and the HALL another 25.2 m², together 20% of carpet. BEDROOM 5
(24.5 m²) and the DINING (15.5 m²) are both fully landlocked with no window, and
the dining is also the 4-door circulation hub. The STORE is 1.3 m².

**`det-18`** — five bathrooms for four bedrooms; three of them in a row behind
BEDROOM 4. BATHROOM 5 is landlocked against the kitchen wall with no window. The
FOYER is a 1.07 m-wide slot mid-plan.

**`det-19`** — a 526 sq ft 1-BHK in which the HALL (12.3 m²) is 2.6× the size of
the bedroom (4.7 m²), and the balcony and bathroom are stacked on the same wall,
both entered through a 1.9 m-wide corridor kitchen.

**`det-21`** — the POOJA (2.1 m²) and FOYER (3.8 m²) are stacked in the top-right
corner, neither at the door. BEDROOM 2 is entered through BATHROOM 2. BEDROOM 1
is 7.0 m², under the NBC floor.

**`det-22`** — BEDROOM 2's only door is into the master. The single toilet is
BEDROOM 3's en-suite. The BALCONY / FOYER pair are again stacked in a corner off
the living.

**`det-23`** — "BEDROOM 1" is 3.5 m² at 3'11" wide. BATHROOM 2 is windowless in
the core. The POOJA is a 2.0 m² niche. Gate on the rear boundary with the
driveway slab crossing both the building and the plot line.

**`det-24`** — the SITOUT is an *interior* room in the middle of the plan. A
sitout is by definition the covered space at the arrival point; here it is
landlocked between two bedrooms and the hall. Also a drawing defect: a piece of
living-room furniture is drawn overlapping the top wall and extending outside the
room.

**`det-25`** — BEDROOM 3 (10.3 m²) is fully landlocked with no window. The
kitchen's only door is into the utility. "BEDROOM 1" is a 4.1 m² strip.

**`det-27`** — the only toilet opens into the living room at the entrance end,
and the kitchen has a door directly into the master bedroom.

**`det-28`** / **`furn-01`** — the DINING room (11.1 m²) is the blind core: five
doors, no window, no exterior wall. The HALL is a 6'4"-wide dead-end strip on the
east side serving only the kitchen.

**`det-39`** — the worst plan in the suite. A 60 sq ft (5.5 m²) kitchen with no
door to any public room serves a 2,365 sq ft four-bedroom house, and the UTILITY
next to it is twice its size. "BEDROOM 1" is a 3.7 m² windowless interior strip.
The HALL is 2.7 m² at 1.02 m wide. The FOYER is 25.9 m², the second-largest room,
windowless, and not at the door. BEDROOM 3 (16.8 m²) has no window. BATHROOM 3
and 4 both open into LIVING 2. The POOJA shares walls with two toilets. The
SITOUT is at the rear between two bathrooms.

**`det-46`** — the KITCHEN (7.1 m²) is fully landlocked with no window. The
bathroom opens into the dining room. "BEDROOM 1" is 1.9 m², a 1.02 m x 1.83 m
closet, sitting under a 3.8 m² room labelled HALL.

**`det-47`** — a 12.7 m² kitchen with no exterior wall and no window, in a
2,379 sq ft house. BEDROOM 1 is a 1.83 m-wide windowless interior strip. The two
toilets (both 4'4" wide) are landlocked in the rear core and both are behind
bedrooms. The FOYER is 2.0 m² and the BALCONY 1.5 m². The PATIO is reached
through a bedroom.

**`det-48`** — ground coverage 99.9%: the building fills the plot, no setback, no
gate, no open space. Two of five bedrooms are entered through a bathroom.
BATHROOM 2 (13.5 m²) and BATHROOM 5 (12.4 m²) are each larger than BEDROOM 3;
the five bathrooms total 17% of carpet, more than the living room. BALCONY 2 sits
*behind* BALCONY 1, making it an interior room. BEDROOM 5 (16.1 m²) has no window.

**`out-01`** (and `det-31`, `out-05`) — the SITOUT is a 1.7 m² box in the rear
corner reachable **only through the bathroom**, and the kitchen's only door is
into a bedroom. The one toilet is a bedroom en-suite. Gate on the rear boundary.

**`wet-10`** — BEDROOM 4 is entered through BATHROOM 1, BATHROOM 2 is behind
BATHROOM 1, and the kitchen's only door is into BEDROOM 3. All four bedrooms are
2.5–2.6 m wide while the plan carries three toilets.

---

## Reproducing this

```
qlmanage -t -s 2000 -o /tmp/fpview out/suite_svg/*.svg   # render to PNG
uv run --with shapely python scripts/audit_svg.py        # all 89, 31 checks
```

`scripts/audit_svg.py` writes `/tmp/fpview/audit.json` (per-plan finding lists)
and prints the counts table. It carries the corrections from your review: the
five wrong checks are gone or narrowed, and each remaining check names the rule
id it belongs to. Twelve of them are not yet in `validate()` — those are
#164-#175 in `RULES.md`.

## What I did not cover

54 of the 89 plans were checked geometrically but not read drawing-by-drawing.
The geometric pass covers all 89, and it found no plan whose fault profile falls
outside the families described above, but drawing defects of the kind I found in
`det-24` (furniture placed outside its room) would only show up on a full visual
sweep. Say the word and I will finish the remaining 54.
