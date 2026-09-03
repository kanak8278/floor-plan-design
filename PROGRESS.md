# Progress log

One line per verified step. Newest last.

- Probed ResPlan (17k plans): 99.75% axis-aligned edges, rooms tile `inner` (overlap ~0%), doors are wall-thickness strips.
- Found wall-mass skeletonisation FAILS (median rebuild IoU 0.37); room-tiling inversion works instead.
- Wrote canonical IR (integer mm, wall centrelines, parametric openings, derived room faces).
- Wrote ResPlan -> IR converter and IR <-> OpenPlan3D Project adapter.
- Verified 300 plans: 100% IR->Project->IR identity, 99.9% openings hosted, 4.2 ms/plan.
- Verified with OpenPlan3D's real TS detectRooms on 200 converted Projects: 0 crashes, 88.5% exact room count, 98.5% within +/-1, per-room area error median 0.000% / p90 0.62%.
- VERDICT: ResPlan is a usable evaluation corpus. Proceed to build.
- Vendored OpenPlan3D source into `vendor/openPlan3D/` (MIT, upstream abb5267, .git stripped so it is versioned as our fork).
- Vendored ResPlan into `data/` (ResPlan.pkl gitignored at 246MB; utils/split/manifest tracked). TODO: switch code paths from /tmp to data/ once agents finish.
- Audited OpenPlan3D docs: FEATURES.md understates the code, BUG_REPORT's "room detection fundamentally limited" is STALE (T-junction splitting now present; measured 88.5% exact). Trust code + measurements, not the repo's docs.
- Found ElevationView.svelte (498 lines) and PrintLayout.svelte (372) — elevations and print layout DO exist, correcting my earlier read.
- Booted OpenPlan3D (vite, port 5199) and loaded converted ResPlan Projects via localStorage `floorplan_projects` + `/editor?id=<id>`.
- Live single-plan test: 2D renders with wall poche, room labels+areas, dimension chains, door swings, window glyphs; 3D extrudes with glass; 0 console errors.
- Batch test, 40 converted plans in the real editor: 0 blank canvases, 0 console errors, wall/door/window counts exact in 39/40, room count exact 85.0%, 3D ok 8/8.
- Room-count mismatches are ALWAYS the editor finding MORE rooms than the IR (never fewer) -> confirms unlabelled circulation space becomes phantom rooms. Not a converter bug.
- OpenPlan3D has walkthrough (PointerLock) but NO clipping planes / section / cutaway. 3D cutaway is genuinely unbuilt.
- Renderer agent landed: 1000 renders (500 plans x 2 modes), 0 exceptions, 0 label collisions, byte-identical across processes, p50 4.8ms. Output is sales-grade: poche, sq ft labels, dimension chains, area statement, room schedule, north arrow, scale bar, title block.
- Built Indian image corpus pipeline: classifier gate (imgclass.py) -> verbatim extraction (extract.py) -> 3 independent checks (imgcorpus.py dual-unit, plausible.py bands + coherence + carpet closure).
- Classifier gate: 7/7 correct on `usable` over a labelled set with hard negatives (elevation, 3D render, photo, detail drawing, isometric plan).
- FOUND AND FIXED my own bug: dimension parser only accepted mm form "4000X3500", so builder plans printing "3.84m x 3.81m" were silently reported as having no dimensions. Human verification of the image caught it; the aggregate metric hid it.
- FOUND AND FIXED: file extensions lie (a .webp that is JPEG, a .webp that is PNG) -> now sniff magic bytes. Also transcode avif/heic.
- FOUND AND FIXED: plausibility checker returned ok=True when zero rooms were measurable. A checker that cannot fail is worthless; absence of data is now an explicit error.
- Real Indian builder plans (Brigade Lakecrest 2BHK, Brigade Belvedere 3BHK) both ACCEPTED. Carpet-area checksum: printed 873 sqft vs computed 864 sqft (1% error). Dual-unit checksum caught a genuine dropped-digit misread (11'5" read as 1'5").
- KEY DATASET INSIGHT: builder/society plans carry dual-unit dimensions AND printed carpet/super-built-up areas, giving three independent checksums. House-plan sites give plot dimensions too. Neither needs manual labelling.
- Rules engine landed: 33 GEO/NBC/BYLAW rules + 9 weighted Vastu rules, 47 tests. Mutation detection 10/10 synthetic, 6/6 topology at 100%. ~2.4ms median. Two-tier reachability (doors, then open thresholds >=1200mm) cut false positives 31 -> 8.
- FOUND AND FIXED my own bug #2: parse_mm only handled metric, so feet-inches-ONLY plans (Divyasree Shettigere: "MASTER BEDROOM 12'0\" X 13'0\"") registered as having no dimensions. Added parse_any().
- Added verification tiers: A = dual units + area total (two checksums); B = single unit + area total (carpet closure only); C = unverifiable, refuse. Tier B is most of the builder corpus and was being wrongly discarded.
- FOUND AND FIXED my own bug #3: the classifier gate rejected on ANY model-reported blocking_problem, including soft caveats like "small text". Non-deterministic -> good plans rejected in one run and accepted in another. Gate now rejects only hard blockers; the deterministic checksums are the real filter.
- Confirmed sonnet and opus agree on plans with genuinely no printed dimensions, so absent data is data, not a model limit.
- Dev server now runs from vendor/openPlan3D (real npm install, persistent). Added /fpeval/ loader page that seeds localStorage so a human can browse converted plans in the real editor. Verified: 12 projects, 0 page errors.
- Solver landed: 29/29 tests, face_recovery IoU = 1.000000 on every success, p50 2.18s. Real INFEASIBLE reporting with closed-form band arguments ("3 rooms need 2400mm clear width, the 5160mm layout admits one per band").
- Converter agent: 17,000/17,000 clean convert->Project->IR, 100% identity. Room count exact in the real editor 86.56% -> 97.08%, within +/-1 99.76%. Openings hosted 99.97%. Persisted 2,500-plan eval corpus (tests/eval_corpus.json).
- THREE OF MY FOUR DIAGNOSES WERE WRONG, refuted by measurement:
  (a) Corridor hypothesis REFUTED. Unlabelled space median 0.0000% of `inner`; corr(extra_faces, unlabelled) = +0.006. The phantom rooms were spurious cycles over a FRAGMENTED wall graph, not voids. Fixing walls took "editor found MORE" 12.68% -> 2.24%.
  (b) Wall fragmentation was NOT mainly linemerge branch points (~4%); it was sub-wall-thickness jogs in source room polygons (18.6% of edges under one wall thickness).
  (c) oriented_envelope warnings were NOT degenerate polygons; GEOS warns for ANY axis-aligned rectangle.
- Learned from the claude-api reference: `temperature` is REMOVED on current models (400 if sent), so determinism cannot come from sampling. Replaced with extract_consensus(): two independent extractions, keep only agreeing fields, disagreements recorded. That is a 4th checksum. Switched extraction to claude-opus-5.
- INTEGRATION TEST PASSES end to end: envelope -> CP-SAT -> validator -> renderer -> Project. 30x40 3BHK OPTIMAL in 0.5s, face IoU 1.0000, 0 validator errors, Project round-trip identity, SVG well-formed. 20x30 3BHK correctly INFEASIBLE (area_budget).
- Generated a real 30x40 3BHK east-facing plan: ground coverage 74.7% under BBMP's 75% cap, area statement, room schedule, north arrow, feet-inch dimension chains. Validator caught NBC.WC_OPENS_INTO_KITCHEN in the solver's own output -- the closed loop working.

## Stairs and room types

- Stairs: FIRST ATTEMPT WAS WRONG. Adding `stair` to ROOM_KEYS took all-rooms-matched 90.40% -> 71.60% on the 250 stair-carrying plans, because stair polygons trace tread outlines (median 10 vertices vs 6 for rooms, max 69) and do not tile cleanly. Measured, reverted.
- Stairs are now `Stair` OBJECTS in the IR, matching OpenPlan3D's model where Stair is a distinct type inside a room. Zero regression: all-rooms-matched back to 90.40%. 277 stairs from 250 plans (width median 1627mm, going 2674mm, 10 risers, 170 straight / 107 L-shaped). Only 7.6% sit inside a labelled room -- the rest are in unlabelled circulation, which is consistent with stairs being 61.5% disjoint from rooms.
- Added `Stair` and `Furniture` to the IR and wired both through the Project adapter. Round-trip identical 150/150.
- NEW: `src/fpeval/roomtypes.py` is the single canonical room-type taxonomy (18 types). Written because three vocabularies had drifted: ResPlan's 6 categories, plausible.py's 17, and free-string `category` on the solver's RoomReq. Each type carries NBC class, carpet/built-up accounting, minima, target range, aspect limit, Vastu zone, window/door/wet flags, OpenPlan3D roomType + floor texture, a furnishing key, and aliases.
- Taxonomy verified against real builder labels: MASTER BEDROOM, TOI-2, PHE SHAFT, PHE & HVAC/VRV, HANDWASH, PUJA, SITOUT, UITILITY, PWD RM, M.TOILET, OTS, MBR, car porch all map correctly; `master_bedroom` correctly beats `bedroom` via longest-alias-first.

## Open

- ResPlan has NO furniture at all, and generated plans are empty rooms. Furnishing system delegated: LLM selects catalogue IDs (closed enum), a relational placement solver owns coordinates. OpenPlan3D's `roomTemplates.ts` cannot be reused -- it hardcodes offsets assuming a 400x300 room.
- LLM spec/brief/patch layer still in progress (spec.py, llm.py, brief.py).

## Feature audit and prompt suite

- `FEATURES_AUDIT.md`: full OpenPlan3D inventory from source (137 lines). Corrected three of my own earlier claims: `motorcycle`/`bike` DO exist (Garage category), `fence_gate` DOES exist (Fencing), multi-floor 3D stacking DOES exist (`buildAllFloorsStacked`), and there IS a cutaway mechanism (`setWallsXray`/`toggleWallTransparency`) though not a clipping-plane section. Also found sun-position/time-of-day simulation, relevant for orientation.
- Catalogue is 189 items across 19 categories, heavily outdoor: Landscaping 57, Outdoor Furniture 13, Paths & Lawns 10, Garden Structures 10, Pool & Spa 8, Garage 8, Fencing 6, plus 8 electrical and 5 plumbing 2D symbols.
- `src/fpeval/suite.py`: prompt-suite schema with machine-checkable ground truth. 39 closed feature tags; validator rejects unknown tags, unknown room types, missing infeasible reasons, and clarify examples that do not say what to ask.
- **110 examples authored across 11 groups**, all validating, every feature tag covered: 90 expect a plan, 10 expect INFEASIBLE with a named binding constraint, 10 expect a clarifying question rather than a guess.
- Ground truth asserts spec recovery, feasibility outcome, programme fit, Vastu zones, and catalogue ids that must be placed -- never geometric similarity to a reference, because there is no single right answer to "3BHK on a 30x40".
- 11 examples use REAL verified builder figures (Brigade Lakecrest 1353/873 sqft, the 3BHK+3T+STUDY at 2184/1310, Divyasree Shettigere 1150/785/733 RERA).
- Thin coverage worth extending later: compound_wall 1, irregular_plot 1, shaft 1, terrace 1, sitout 2, coverage 2, setbacks 2, rwh 2.
- LLM layer landed: 100% schema conformance on 28 prompts, 96.4% every-field-correct, 0/28 invented a plot size, 6/6 asked on underdetermined, 0/4 faked a plot for an apartment. 19 patch ops with coordinate emission rejected in code, not merely discouraged.

## HTTP API

- Before this there was NO api. The only connection was a one-way file drop: Python wrote `static/fpeval/projects.json`, a Svelte route fetched it into `localStorage['floorplan_projects']`, and `/editor?id=X` read localStorage. No read-back path at all.
- Built `service/app.py` (FastAPI, stateless) + a same-origin SvelteKit proxy at `api/[...path]/+server.ts`, so the browser only ever talks to one host. Stateless on purpose: the document lives in the browser, so there is no server/client desync class of bug and the editor's undo stack stays the single source of history.
- Endpoints: `GET /api/health`, `GET /api/roomtypes` (the 18-type taxonomy, so the UI never hard-codes a room list), `POST /api/generate`, `POST /api/validate`, `POST /api/render`.
- Verified: generate 30x40 3BHK -> OPTIMAL in 4.1s, 8 rooms / 11 walls / 8 doors / 7 windows, 48KB SVG, area statement; validate 3ms; render annotated 38KB. All three work through the proxy on :5199 as well as direct on :8099.
- FOUND AND FIXED a real read-back bug in the process: /api/generate reported 0 errors while /api/validate on the SAME plan reported 9. Cause -- OpenPlan3D's `Project` stores a room as `walls: string[]` and derives the outline on the fly, so it carries no room polygons; `from_project` returned rooms with 0 vertices and the validator flagged GEO.ROOM_DEGENERATE for every one. `from_project` now derives faces from the wall graph the way `detectRooms` does. Generate and validate now agree exactly (0 errors, 7 warnings both sides).
- Regression: ResPlan round-trip still 400/400 identical on walls, openings, rooms and plot; 99.9% of rooms recover a polygon on read-back.

## Integrated chat agent (branch `chat-agent`)

- Decided the substrate: server-authoritative, event-sourced document; one Command vocabulary for mouse and model; FastAPI service + SvelteKit BFF proxy; tabbed right dock. Written up in `CHAT_AGENT_DESIGN.md`.
- FOUND: room identity in OpenPlan3D is derived and unstable. `roomDetection.ts:228` mints ids as `room-${n}-${Date.now()}`, and a name survived only while the room's EXACT wall-id set was unchanged. `splitWall()` alone turned "Master Bedroom" back into "Room 3". Fatal for a chat agent whose whole vocabulary is room names.
- FOUND: the IR<->Project adapter was one-way. `from_project()` read only `activeFloorId` and dropped columns, guides, measurements, dimension + text annotations, groups, entourage, background image, per-wall colours/textures, `curvePoint`, and every other storey. `ir_identity` stayed green throughout because it only compares wall geometry, opening parameters, room labels, and the plot -- the "17,000/17,000 identity" result is real but much narrower than losslessness.
- Room identity now rides on an ANCHOR: a point inside the room, recorded when it is first named; whichever face contains it is that room. Splitting a wall does not move the point. Implemented twice on purpose (`roomIdentity.ts` for the optimistic client, `roomid.py` for the authoritative service) and held together by a conformance test rather than by discipline.
- `roomDetection.ts` deliberately untouched, so the byte-for-byte vendor parity check in `tests/js/verify.ts` still means what it says.
- CONFORMANCE TEST EARNED ITS KEEP IMMEDIATELY: minted room ids diverged across languages because each side hashed the centroid in its own units (mm vs cm). Now canonicalised on millimetres both sides. Reading the code would not have caught this.
- IR extended to a superset of the editor's model: `Presentation` block (guides, measurements, dimensions, texts, groups, entourage, background), `Column`, wall finishes + `curvePoint`, opening subtype/swing/flip, room class/texture/colour/label offset/anchor, furniture colour/material/scale, and a `Design` wrapper for multi-storey so `Plan` stays single-storey and every rule, solver, and renderer keeps working unchanged.
- Derived defaults needed care: a bathroom's floor texture is derived from its category, so the IR has two states where Project has one field. Overrides are recorded in the sidecar; editor-authored documents fall back to comparing against the derived value, and the residual ambiguity (an override equal to its own default) is invisible because both states emit the same field.
- Adapter verified: 1,000 real ResPlan plans, 0 `ir_identity` failures, 0 round-trip idempotence failures, 0 dataclass-equality failures, ~7 ms/plan. Plus 26 field-by-field tests over a two-storey fixture carrying every field OpenPlan3D can express.
- Tests: 127 Python pass (26 round-trip, 15 identity incl. 2 cross-language), 10 TypeScript pass. The last TS test asserts the OLD wall-set rule FAILS the split case, so the suite is known to discriminate rather than merely be green.
- NOTE for whoever owns test_rules.py: `test_false_positive_rate_on_real_plans` asserts median latency < 10 ms and fails under concurrent CPU load (it failed once here with svelte-check running alongside, passed immediately when idle). A wall-clock bound in a correctness test is a tripwire.
- NOTE: `svelte-check` reports 6 pre-existing errors in `BuildPanel.svelte` -- `Tool` is typed without `'measure'`/`'annotate'` but the panel compares against both, so those tools are set through a path the type does not know about. Upstream's, not introduced here.

## Command bus, service, and chat UI (branch `chat-agent`)

- One command vocabulary for the mouse and the model: 47 commands in `commands.py`, split into `symbolic` (agent-authorable, never carries a coordinate) and `direct` (user-only, carries coordinates). DECISIONS.md #6 is now enforced by `Command.validate` against `source`, not hoped for.
- Commands carry the ids they create and read no clock, so replaying the log reproduces the document exactly. `Document.verify_log()` asserts it and is exposed at `/api/designs/{id}/verify` -- the answer depends on real user sequences, not the ones I thought to write down.
- One applier (`apply.py`, 47 handlers). The browser still applies locally for pointer-speed response, but that is a *prediction*; the service's answer is authoritative and a hash mismatch means "take theirs". Writing a second applier in TypeScript would have meant two implementations of every edit.
- Every store mutator in `project.ts` now records a command -- 61 sites. `tests/test_commands_ts.py` fails if a mutator changes the design without recording one; I wrote that test after missing four by hand, and it immediately caught `scaleFurniture` recording *inside* the `mutate()` closure.
- `generated.ts` is generated from the Python table by `python -m fpeval.gen_ts`, and a test runs `--check`. A hand-maintained second copy is how a client comes to emit `add_window` while the service implements something else, typechecking on both sides.
- Gestures coalesce in the bus rather than needing an end-of-drag hook (there is none: `beginDrag` and `commitFurnitureMove` both fire at the *start*). Sixty pointer moves become one command. `move_wall_by` carries a cumulative delta so collapsing frames cannot lose part of the movement.
- FOUND AND FIXED: the adapter renamed the editor's floor on every round trip (`f9k2p1` -> `floor-f9k2p1`), which would have broken `activeFloorId` and every storey reference. My own round-trip fixtures used *our* `floor-<id>` convention, so they never noticed -- the exact blind spot I had just criticised in `ir_identity`. Added an editor-shaped fixture.
- FOUND AND FIXED: `adopt` replaced a document the service was already holding while keeping its transcript, so the assistant remembered edits the document had lost. It read as the model hallucinating; it was the service discarding state. Re-attach now returns the existing log and projection.
- FOUND AND FIXED: `reconcileRooms` documented "later entries win ties" but compared with `<=`, so the incumbent won. The caller passes last pass's on-screen rooms first and the saved rooms last, with identical areas -- so a room the assistant had just renamed kept reading "Room 1" on the plan while the chat said otherwise. Precisely the two-systems failure the shared document exists to prevent. Fixed in both languages, with a test in each.
- FOUND AND FIXED: room detection was cached on wall geometry alone, so renaming a room did not invalidate it and the old labels stayed up. Identity lives in the saved rooms, so a change to them has to bust the cache.
- FOUND AND FIXED a live upstream bug, not one of mine: `FloorPlanCanvas.onKeyDown` called `preventDefault()` on `Space` *before* computing its form-field guard, so no text field in the app could accept a space -- including the room-rename box. One shared `isTypingTarget` helper now gates every global handler.
- Chat agent (`agent.py`): manual tool-use loop on `claude-opus-5`, adaptive thinking, streaming. Tools are `get_plan`, `get_findings`, `apply_commands`; only symbolic commands are offered. The prompt's command catalogue is generated from the table, so it cannot drift from what the applier accepts.
- Context layout: frozen system + sorted tools form a stable cache prefix; the volatile per-turn payload (plan digest + events since the model last spoke) goes in as a **mid-conversation system message**, which preserves the cached prefix and means plan state cannot be forged from a user message. Measured 12,898 cached tokens read on a second turn.
- Right dock landed: Chat | Properties | Layers as tabs, collapsible to a rail, in the app's own palette so `html.dark` carries it. `PropertiesPanel` and `LayersPanel` gained a `docked` mode; standalone they still position themselves.
- END TO END, VERIFIED IN A BROWSER: drew a plan, asked "call the left room the master bedroom and the right one the living room, put a door between them, and tell me what the code checks say". Three commands applied, the door drawn with its swing arc, both names on the canvas, the feed showing all three with generated summaries ("Unnamed room renamed to Master Bedroom, 4.80 x 7.00 m"), and the whole thing intact after a page reload. 0 console errors.
- The agent's reply was substantively right without being told any of it: it noticed the site has no north set so "east" is undefined, flagged the missing front door as the one hard error, and explained the Brahmasthan warning as a consequence of a centred dividing wall.
- Tests: 221 Python, 12 TypeScript. 500 real ResPlan plans still round-trip clean at 2.27 ms/plan.
