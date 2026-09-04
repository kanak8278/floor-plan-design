# Hand annotation of the Indian plan corpus

Purpose: a **reconstruction target**. For each real drawing we hand-write the brief a
client would have given, and the truth that brief must produce. Then we run
prompt -> spec -> programme -> solve -> render and compare against the drawing.
The comparison answers one question: *is the capability there at all.*

Nothing here is generated. Every number is read off the image by eye and
cross-checked against the sheet's own arithmetic before it is written down.

## The capability ladder

One image-vs-image verdict returns "no" 21 times and names nothing. Compare in
rungs instead, so each failure identifies the missing capability:

| Rung | Compare | A failure means |
|---|---|---|
| 1 | `prompt` -> `extract_spec` vs `spec_truth` | the brief cannot be *said* (vocabulary/schema gap) |
| 2 | `spec_truth` -> `spec_to_programme` warnings | the programme cannot be *held* (rooms silently dropped) |
| 3 | solved plan vs `graph` | the topology cannot be *reproduced* (adjacency, doors, windows) |
| 4 | solved plan vs `rooms` + `envelope` | the geometry cannot be *reproduced* |

Rungs 1 and 2 need no solver and no API. Run them first: they are where the
"functionality is not there" answers actually live.

Rung 4 is reported as **expressible / not expressible**, not as a score. A
slicing-tree solver cannot emit a re-entrant envelope, a cantilevered balcony, or
a split floor level, so scoring it as a near-miss would be dishonest.

## Reading rules

1. **The drawing outranks the filename.** `godrej-ihp-3.5-bhk-floor-plan.webp`
   is a 2BHK. Ignore filenames entirely.
2. **Metric outranks feet-inches** when a sheet prints both and they disagree.
   The metric figures are the CAD dimensions; the imperial column is converted by
   hand and is wrong on real sheets. Every disagreement goes in `sheet_errors`.
3. **Only what is printed.** No north arrow on the sheet means
   `road_facing_side: null` and the prompt must not mention a facing.
4. **The unit boundary is not the sheet boundary.** Lift lobbies, fire towers,
   janitor closets and garbage rooms belong to the building, not the dwelling.
   They are recorded in `outside_unit`, never in `rooms`.
5. **Illegible is `null`, never a guess.** A dimension read at low confidence gets
   `"uncertain": true` and is excluded from area closure.
6. **The prompt asserts exactly the truth and nothing more.** If the truth does
   not carry a facing, the prompt does not say one. This is what makes rung 1 a
   real test rather than a paraphrase check.

## Verification before a file is committed

A truth file is only accepted once the sheet's own numbers agree with it:

- room `w_mm x d_mm` reproduces every printed area sub-total (balcony, utility)
  to within rounding;
- the labelled rooms plus circulation account for the printed carpet area;
- categories are keys that exist in `roomtypes.py` — no invented vocabulary.

Both closures are recorded in `closure`. A file that does not close is not
ground truth; it is a negative control, marked `"usable": false` with the reason.

## Schema

See the committed files. Fields are named to match `spec.py` `DesignSpec` so
rung 1 is a direct comparison, not a mapping exercise.
