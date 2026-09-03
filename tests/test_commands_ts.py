"""Does the browser speak the same command vocabulary as the service?

Three drift risks, one test each.

  **A stale generated file.** The vocabulary lives in `commands.py`, next to
  the applier. `generated.ts` is the browser's view of it. If they diverge, a
  client emits `add_window` while the service implements something else, and
  both sides typecheck.

  **An op name that does not exist.** `record('update_door', ...)` in the store
  is a plain string. A typo there is invisible until a user makes that edit.

  **An uninstrumented mutator.** A store function that changes the design
  without recording a command is a change the service never hears about and
  the chat never shows. I wrote this test after missing four of them by hand.
"""
from __future__ import annotations
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from fpeval.commands import TABLE                              # noqa: E402
from fpeval.gen_ts import TARGET, render                       # noqa: E402
from fpeval.commands import table_manifest                     # noqa: E402

STORE = os.path.join(ROOT, "vendor/openPlan3D/src/lib/stores/project.ts")


def _store_source() -> str:
    with open(STORE) as fh:
        return fh.read()


# --------------------------------------------------------------------------
# the generated file
# --------------------------------------------------------------------------

def test_generated_ts_is_not_stale():
    assert TARGET.exists(), f"{TARGET} missing; run python -m fpeval.gen_ts"
    expected = render(table_manifest())
    actual = TARGET.read_text()
    assert actual == expected, (
        "vendor/openPlan3D/src/lib/commands/generated.ts is stale -- "
        "run `python -m fpeval.gen_ts`")


def test_generated_ts_covers_every_command():
    text = TARGET.read_text()
    for op in TABLE:
        assert f"'{op}'" in text, f"{op} missing from generated.ts"


# --------------------------------------------------------------------------
# op names used in the store
# --------------------------------------------------------------------------

RECORD_CALL = re.compile(r"record\(\s*'([a-z_]+)'")


def test_every_recorded_op_exists():
    ops = set(RECORD_CALL.findall(_store_source()))
    assert ops, "no record() calls found -- has the store been instrumented?"
    unknown = sorted(ops - set(TABLE))
    assert not unknown, f"the store records commands that do not exist: {unknown}"


def test_the_store_exercises_a_real_share_of_the_vocabulary():
    """A sanity floor, not a target. If this drops sharply, someone has
    replaced instrumented mutators with uninstrumented ones."""
    ops = set(RECORD_CALL.findall(_store_source()))
    assert len(ops) >= 25, f"only {len(ops)} distinct commands recorded: {sorted(ops)}"


# --------------------------------------------------------------------------
# mutator coverage
# --------------------------------------------------------------------------

FUNCTION = re.compile(r"^export function (\w+)\(", re.MULTILINE)

# Functions that change the document but deliberately record nothing, with the
# reason. Anything not listed here and not recording a command fails the test
# below -- the list is the point, not the exemption.
EXEMPT = {
    # History navigation. These replay the log rather than adding to it, and
    # `Document.undo` is the server-side equivalent.
    "undo": "replays history, does not add to it",
    "redo": "replays history, does not add to it",
    "jumpToUndoStep": "replays history, does not add to it",
    "beginUndoGroup": "undo bookkeeping, no state change",
    "endUndoGroup": "undo bookkeeping, no state change",
    "beginDrag": "snapshot only, no state change",
    "commitFurnitureMove": "snapshot only, no state change",
    "endWallDrag": "gesture bookkeeping, no state change",
    # Whole-document operations. These replace the document rather than editing
    # it, so the service adopts a new one instead of applying a command.
    "loadProject": "replaces the whole document",
    "createDefaultProject": "constructs a document",
    "createDefaultFloor": "constructs a floor",
    # NOT YET COMMAND-BACKED -- real gaps, listed so they stay visible.
    "importFloorIntoCurrentProject": "bulk import; needs its own command",
    "addCustomEntourage": "uploads a project-level asset; needs its own command",
    # Read-only helpers.
    "findGroupForElement": "read-only",
}


def _function_bodies(src: str) -> dict[str, str]:
    """Crude but adequate: from each `export function` to the next one."""
    starts = [(m.group(1), m.start()) for m in FUNCTION.finditer(src)]
    out: dict[str, str] = {}
    for i, (name, pos) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(src)
        out[name] = src[pos:end]
    return out


MUTATES = ("mutate(", "currentProject.set(", "snapshot(")


def test_every_mutator_records_a_command():
    bodies = _function_bodies(_store_source())
    missing = []
    for name, body in bodies.items():
        if name in EXEMPT:
            continue
        if not any(m in body for m in MUTATES):
            continue                       # not a mutator
        if "record(" not in body:
            missing.append(name)
    assert not missing, (
        "these store functions change the design without recording a command, "
        "so the service never hears about the change and the chat never shows "
        f"it: {sorted(missing)}")


def test_exempt_list_has_no_dead_entries():
    """An exemption for a function that no longer exists hides the next one."""
    names = set(_function_bodies(_store_source()))
    stale = sorted(set(EXEMPT) - names)
    assert not stale, f"EXEMPT lists functions that no longer exist: {stale}"


def test_record_is_never_called_inside_a_mutate_closure():
    """`mutate(fn)` runs `fn` against the floor; recording in there couples the
    log to the mutation callback and fires on replay paths. I made exactly this
    mistake in `removeTextAnnotation`."""
    src = _store_source()
    offenders = []
    for m in re.finditer(r"mutate\(", src):
        depth, i, n = 0, m.end() - 1, len(src)
        # Walk to the matching paren of mutate(...)
        while i < n:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if "record(" in src[m.end():i]:
            line = src.count("\n", 0, m.start()) + 1
            offenders.append(line)
    assert not offenders, (
        f"record() called inside a mutate() closure at line(s) {offenders}")


# --------------------------------------------------------------------------
# units, at the one boundary where they change
# --------------------------------------------------------------------------

def test_the_store_never_sends_raw_centimetres_as_a_position():
    """Commands are millimetres. A `position:` built without `pt()` or `mm()`
    is a hundredfold error that renders as a plan a metre wide."""
    src = _store_source()
    bad = []
    for m in re.finditer(r"record\('[a-z_]+',\s*\{[^}]*?\}", src, re.S):
        blob = m.group(0)
        for field in ("position:", "start:", "end:"):
            if field not in blob:
                continue
            after = blob.split(field, 1)[1][:80]
            if "pt(" not in after and "mm(" not in after:
                line = src.count("\n", 0, m.start()) + 1
                bad.append((line, field))
    assert not bad, f"unconverted coordinates in record() calls: {bad}"


@pytest.mark.skipif(not os.path.exists(os.path.join(
    ROOT, "vendor/openPlan3D/node_modules")), reason="npm install not run")
def test_gen_ts_check_mode_agrees():
    """The command a developer actually runs."""
    proc = subprocess.run(
        [sys.executable, "-m", "fpeval.gen_ts", "--check"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")})
    assert proc.returncode == 0, proc.stderr or proc.stdout
