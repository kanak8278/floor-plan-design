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
