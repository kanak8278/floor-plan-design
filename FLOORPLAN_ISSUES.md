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


---

## The per-plan list

Not kept here. It was 1,489 lines of pre-correction findings, and 146 of them
were false by the correction table above. `scripts/audit_svg.py` regenerates
the current list from the SVGs in `out/suite_svg` whenever it is wanted, so a
stale copy in the repo is worse than none.
