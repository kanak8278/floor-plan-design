"""Generate the TypeScript view of the command vocabulary.

    python -m fpeval.gen_ts            # write it
    python -m fpeval.gen_ts --check    # fail if it is stale

The vocabulary is defined once, in `commands.py`, because that is where the
applier is. The browser needs the same names and the same param lists to build
a command, and hand-maintaining a second copy is how you end up with a client
emitting `add_window` while the service implements `add_opening` -- a bug that
typechecks on both sides and only shows up in production.

`tests/test_commands_ts.py` runs `--check`, so the generated file cannot drift.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .commands import table_manifest

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "vendor/openPlan3D/src/lib/commands/generated.ts"

HEADER = """\
/**
 * GENERATED FILE -- do not edit.
 *
 *   python -m fpeval.gen_ts
 *
 * The command vocabulary is defined in `src/fpeval/commands.py`, next to the
 * applier that implements it. This is the browser's view of the same table.
 * `tests/test_commands_ts.py` fails if the two drift.
 */
"""


def render(manifest: dict) -> str:
    cmds = manifest["commands"]
    enums = manifest["enums"]

    out = [HEADER]
    out.append(f"export const VOCABULARY_VERSION = {manifest['version']};\n")

    out.append("/** Every command name the service implements. */")
    names = ", ".join(f"'{op}'" for op in sorted(cmds))
    out.append(f"export type CommandOp =\n  | " + "\n  | ".join(
        f"'{op}'" for op in sorted(cmds)) + ";\n")
    out.append(f"export const COMMAND_OPS: CommandOp[] = [{names}];\n")

    out.append("export type CommandFamily = 'symbolic' | 'direct';")
    out.append("export type CommandSource = "
               + " | ".join(f"'{s}'" for s in enums["sources"]) + ";\n")

    out.append("""export interface CommandSpec {
  family: CommandFamily;
  required: string[];
  optional: string[];
  /** The store function the optimistic client uses, if it has one. */
  editor: string | null;
  doc: string;
}
""")
    out.append("export const COMMANDS: Record<CommandOp, CommandSpec> = {")
    for op in sorted(cmds):
        e = cmds[op]
        editor = f"'{e['editor']}'" if e["editor"] else "null"
        doc = e["doc"].replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
        out.append(f"  '{op}': {{")
        out.append(f"    family: '{e['family']}',")
        out.append(f"    required: {json.dumps(e['required'])},")
        out.append(f"    optional: {json.dumps(e['optional'])},")
        out.append(f"    editor: {editor},")
        out.append(f"    doc: '{doc}',")
        out.append("  },")
    out.append("};\n")

    out.append("""/**
 * Param names that count as absolute geometry. A `symbolic` command may not
 * carry one: the model emits intent, solvers emit coordinates
 * (DECISIONS.md #6). The service enforces this too — this copy is so the
 * client can refuse before making a round trip.
 */""")
    out.append("export const COORDINATE_KEYS: ReadonlySet<string> = new Set("
               + json.dumps(manifest["coordinate_keys"]) + ");\n")

    for name, values in sorted(enums.items()):
        if name == "sources":
            continue
        const = name.upper()
        out.append(f"export const {const} = {json.dumps(values)} as const;")
    out.append("")

    out.append("""/** Is this command shaped correctly? Mirrors `Command.validate` minus the
 *  referential checks, which need the document and so happen server-side. */
export function validateShape(
  op: string,
  params: Record<string, unknown>,
  source: CommandSource = 'user',
): string[] {
  const spec = (COMMANDS as Record<string, CommandSpec>)[op];
  if (!spec) return [`unknown command '${op}'`];
  const errors: string[] = [];
  if (spec.family === 'symbolic') {
    for (const key of Object.keys(params)) {
      if (COORDINATE_KEYS.has(key)) {
        errors.push(`${op}: '${key}' is a coordinate, and ${op} is symbolic`);
      }
    }
  }
  if (spec.family === 'direct' && source === 'agent') {
    errors.push(`${op}: an agent may not author a direct command`);
  }
  const allowed = new Set([...spec.required, ...spec.optional]);
  for (const key of spec.required) {
    if (params[key] === undefined || params[key] === null) {
      errors.push(`${op}: missing required param '${key}'`);
    }
  }
  for (const key of Object.keys(params)) {
    if (!allowed.has(key)) errors.push(`${op}: unexpected param '${key}'`);
  }
  return errors;
}
""")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the file on disk is stale")
    ap.add_argument("--out", type=Path, default=TARGET)
    args = ap.parse_args(argv)

    text = render(table_manifest())
    if args.check:
        if not args.out.exists():
            print(f"{args.out} does not exist; run python -m fpeval.gen_ts",
                  file=sys.stderr)
            return 1
        if args.out.read_text() != text:
            print(f"{args.out} is stale; run python -m fpeval.gen_ts",
                  file=sys.stderr)
            return 1
        print(f"{args.out} is up to date")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"wrote {args.out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
