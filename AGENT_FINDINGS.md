# Agent findings

Open items only. Severity: **BUG** wrong behaviour · **LIMIT** the vocabulary
cannot express it · **UX** it works but reads badly. Written by
`scripts/probe_agent.py` runs plus reading the replies; `--help` explains the
outcomes. Fixed items are in git history and in `PROGRESS.md`.

## Open

- LIMIT **Two appliers exist and overlap on geometry.** `apply.py` takes `commands.Command` + `Design` (document service, chat agent); `apply_ops.py` takes `llm.PatchOp` + `Plan` (repair loop). They should converge. The graph-aware wall move is imported rather than copied, but the rest has not converged, and the compass-vocabulary bug below is exactly what the duplication produces.
- LIMIT **No brief-only variant of `add_room`.** Its contract is "add a programme entry, then re-solve", so a user recording an intention without regenerating geometry cannot be served. The agent identifies this precisely and declines rather than re-solving unasked.
- LIMIT **The agent cannot reject its own edit.** `rooms_gone` is reported, but there is no command to restore a destroyed room — only to narrate the loss. `Document.undo` exists; it is not in the symbolic vocabulary.
- UX **`move_opening` cannot say "within the current room pair".** Moving an opening to a named fraction can silently re-plumb circulation. The agent caught that moving `o0` to the quarter point would make the living open into the study and strand the hall, and refused — correct, but "move it within its current room pair" is what a user usually means and the vocabulary has no way to say it.
- UX **`--fresh-document` without `--fresh-conversation`** shows the agent a document that contradicts its own transcript. It notices ("the document has reverted again") and wastes a turn. The two flags should be coupled.

## Two lessons about probing a mutating system

- **A probe must assert what is preserved, not just what changed.** The first `move/wall` probe asserted only "did a wall move", so it passed while destroying the document.
- **Isolate by default.** Probes shared one cumulative document, so wall-move damage poisoned every later probe; most of the 8 refusals in that run were the agent correctly declining to work on a genuinely broken plan. Attribution needs `--fresh-document`.

## What the agent gets right, and should keep getting right

- It reports "nothing changed and the document is still at seq 0" rather than claiming success. Across 45 probes the failure mode that matters most — asserting an effect the document does not show — did not occur once.
- Refusals cite opening ids, positions along walls, clear widths, and the rule that would break. Three caught defects in the probe wording rather than doing the wrong thing.
- It diagnosed the stubbed programme layer unaided across three turns, then refused later destructive ops because it had observed re-solves corrupting the document.
