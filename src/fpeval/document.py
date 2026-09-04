"""The design document: a design, plus the ordered log that produced it.

This is the "single underlying baseline" the whole system is arranged around.
A mouse drag and an agent's repair both arrive as a `Command`, both go through
one applier, and both leave one `Event` behind. Nothing edits the design by any
other route, so there is no path by which the browser and the service can come
to disagree.

Three things fall out of that rather than needing to be built:

  **Undo and time travel.** `at_seq(n)` replays the log to any point. The
  editor's own undo stack is 50 in-memory entries wiped by clearing browser
  data; this is the history a design actually needs.

  **The chat feed.** `Event.summary` is generated from the command, so the
  bullet list the user reads is a projection of the log, not a parallel thing
  someone has to remember to append to.

  **Conflict handling.** An agent computes a patch against `seq = N` and it may
  land at `seq = N + k`. Referential validation happens at apply time, so the
  ops whose referents survived apply and the rest come back with a reason --
  see `apply.apply_all`.

`state_hash()` is what the optimistic client reconciles against. The client
applies a command locally so the canvas responds immediately, sends it, and
compares hashes with the reply; a mismatch means "take the server's
projection", and the cost of the client being wrong is one repaint rather than
a corrupted document.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from .apply import ApplyResult, apply_command
from .commands import Command, Event
from .ir import Design, Plan, Site
from .project import to_project, design_from_project

SNAPSHOT_EVERY = 50          # commands between stored snapshots


def _canonical(obj: Any) -> Any:
    """Strip everything a hash must not depend on.

    Floats are the interesting case: `north_deg` and `rotation` are floats, and
    `0.1 + 0.2` on one machine must hash the same as `0.30000000000000004` on
    another. They are rounded to 6 decimals, which is far finer than any angle
    a plan cares about and coarse enough to be stable.
    """
    if isinstance(obj, dict):
        return {k: _canonical(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        r = round(obj, 6)
        return int(r) if r == int(r) else r
    return obj


def state_hash(design: Design) -> str:
    """A content hash of the design. Stable across processes and machines."""
    blob = json.dumps(_canonical(design.to_dict()), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


@dataclass
class LogEntry:
    seq: int
    command: Command
    event: Event
    hash_after: str
    # Out-of-band input the command needed, kept so replay does not have to
    # recompute it. Only `replace_storey` uses one today, and it is a solved
    # `Plan`: CP-SAT under a wall-clock limit is not reproducible across
    # machines or load, so re-solving during replay would produce a *different*
    # floor. Before this field, `at_seq` replayed `replace_storey` with no
    # payload, the applier refused, and the failure was swallowed -- one undo
    # after a solve silently discarded the solved storey.
    payload: Any = None

    def to_dict(self) -> dict:
        # The payload is deliberately not serialised. It is geometry, and the
        # design blob beside the log already holds the geometry; writing it
        # twice would double the size of every stored document. The cost is
        # that undo across a process restart cannot replay a solve, which
        # `verify_log` reports rather than hides.
        return {"seq": self.seq, "command": self.command.to_dict(),
                "event": self.event.to_dict(), "hash_after": self.hash_after,
                "has_payload": self.payload is not None}


@dataclass
class Rejection:
    command: Command
    errors: list[str]

    def to_dict(self) -> dict:
        return {"command": self.command.to_dict(), "errors": list(self.errors)}


@dataclass
class Document:
    design: Design
    log: list[LogEntry] = field(default_factory=list)
    # (seq, design) checkpoints so replay does not start from nothing.
    snapshots: list[tuple[int, Design]] = field(default_factory=list)
    base: Optional[Design] = None      # the design at seq 0

    def __post_init__(self) -> None:
        if self.base is None:
            self.base = copy.deepcopy(self.design)

    # ---------------------------------------------------------------- state

    @property
    def seq(self) -> int:
        return self.log[-1].seq if self.log else 0

    @property
    def hash(self) -> str:
        return self.log[-1].hash_after if self.log else state_hash(self.design)

    @property
    def events(self) -> list[Event]:
        return [e.event for e in self.log]

    def events_since(self, seq: int) -> list[Event]:
        """What has happened since the model last saw the document.

        This is the payload that goes into the agent's per-turn context, and it
        is deliberately not the whole history: state carries correctness,
        history only carries intent ("make *it* bigger"). An unbounded bullet
        list would eat the context window for no gain.
        """
        return [e.event for e in self.log if e.seq > seq]

    # ---------------------------------------------------------------- apply

    def apply(self, cmd: Command, *, payload: Any = None) -> ApplyResult:
        """Apply one command, appending to the log if it succeeds."""
        seq = self.seq + 1
        res = apply_command(self.design, cmd, payload=payload, seq=seq)
        if not res.ok or res.event is None:
            return res
        cmd.seq = seq
        res.event.at = _now()
        self.design = res.design
        self.log.append(LogEntry(seq=seq, command=cmd, event=res.event,
                                 hash_after=state_hash(res.design),
                                 payload=payload))
        if seq % SNAPSHOT_EVERY == 0:
            self.snapshots.append((seq, copy.deepcopy(res.design)))
        return res

    def apply_batch(self, cmds: Sequence[Command], *,
                    payloads: Optional[dict[str, Any]] = None
                    ) -> tuple[list[Event], list[Rejection]]:
        """Apply a batch, keeping what works.

        Partial application is the whole point for an agent patch: six ops
        where two reference a wall the user just deleted should land four and
        explain two.
        """
        events: list[Event] = []
        rejected: list[Rejection] = []
        for cmd in cmds:
            res = self.apply(cmd, payload=(payloads or {}).get(cmd.id))
            if res.ok and res.event is not None:
                events.append(res.event)
            else:
                rejected.append(Rejection(cmd, res.errors))
        return events, rejected

    # -------------------------------------------------------------- history

    def at_seq(self, seq: int) -> Design:
        """The design as it was after `seq` commands.

        Replays from the nearest snapshot at or below `seq`. Commands are
        deterministic and carry the ids they create, so this reproduces the
        exact document rather than an equivalent one.
        """
        if seq < 0:
            raise ValueError("seq must be >= 0")
        if seq >= self.seq:
            return copy.deepcopy(self.design)
        best_seq, best = 0, self.base
        for s, snap in self.snapshots:
            if s <= seq and s >= best_seq:
                best_seq, best = s, snap
        design = copy.deepcopy(best) if best is not None else Design(id="")
        for entry in self.log:
            if entry.seq <= best_seq or entry.seq > seq:
                continue
            res = apply_command(design, entry.command, payload=entry.payload,
                                seq=entry.seq)
            if res.ok:
                design = res.design
        return design

    def undo(self) -> Optional[Event]:
        """Drop the last command and rebuild. Returns the event undone.

        Rebuilding rather than inverting: an inverse for every command is a
        second applier to keep correct, and the whole design here is that there
        is only one.
        """
        if not self.log:
            return None
        undone = self.log[-1]
        target = undone.seq - 1
        self.design = self.at_seq(target)
        self.log = [e for e in self.log if e.seq <= target]
        self.snapshots = [(s, d) for s, d in self.snapshots if s <= target]
        return undone.event

    def verify_log(self) -> list[str]:
        """Does replaying the log actually reproduce the current design?

        The integrity check for the whole mechanism. If this ever fails, some
        command is not deterministic -- reading a clock, minting an id, or
        depending on iteration order -- and undo and time travel are quietly
        broken everywhere.
        """
        problems: list[str] = []
        design = copy.deepcopy(self.base) if self.base else Design(id="")
        for entry in self.log:
            res = apply_command(design, entry.command, payload=entry.payload,
                                seq=entry.seq)
            if not res.ok:
                problems.append(f"seq {entry.seq} ({entry.command.op}) no "
                                f"longer applies: {res.errors[0]}")
                continue
            design = res.design
            got = state_hash(design)
            if got != entry.hash_after:
                problems.append(f"seq {entry.seq} ({entry.command.op}) replays "
                                f"to {got} but was logged as {entry.hash_after}")
        if state_hash(design) != self.hash:
            problems.append("full replay does not reproduce the current design")
        return problems

    # ----------------------------------------------------------- projection

    def projection(self, name: Optional[str] = None) -> dict:
        """The document as OpenPlan3D `Project` JSON, for the editor."""
        proj = to_project(self.design, name=name or self.design.name or None)
        proj[".fpeval_doc"] = {"seq": self.seq, "hash": self.hash}
        return proj

    def to_dict(self, *, with_log: bool = True) -> dict:
        out: dict[str, Any] = {
            "design": self.design.to_dict(),
            "seq": self.seq,
            "hash": self.hash,
        }
        if with_log:
            out["log"] = [e.to_dict() for e in self.log]
            out["base"] = self.base.to_dict() if self.base else None
        return out

    # --------------------------------------------------------- constructors

    @staticmethod
    def _adopt(design: Design) -> Design:
        """Make a design addressable, in place.

        Every room gets an anchor and every wall-bounded storey gets its faces
        derived. Both are what identity rides on, and a design that arrives
        without them loses every room name on its first geometry edit.

        That is not hypothetical: `from_plan` skipped this, so a plan straight
        out of the solver had zero anchors, and one `move_wall_parallel`
        dropped all seven room names -- reconciliation fell through to
        matching by exact wall set, which a wall move invalidates by
        definition. `scripts/probe_agent.py` caught it; the tests below pin it.

        Deliberately NOT called when loading from storage. A stored document's
        rooms already carry anchors, and mutating a design on load would
        change its hash and make `verify_log` disagree with what was written.
        """
        from .roomid import ensure_anchors
        from .faces import rederive_rooms
        for st in design.storeys:
            if st.walls and not st.rooms:
                st.rooms, _gone, _fresh = rederive_rooms(st)
            ensure_anchors(st.rooms)
        return design

    @classmethod
    def empty(cls, design_id: str, *, name: str = "",
              north_deg: float = 0.0) -> "Document":
        ground = Plan(id=f"{design_id}-g", level=0, name="Ground Floor",
                      site=Site(north_deg=north_deg))
        design = Design(id=design_id, storeys=[ground],
                        active_storey_id=ground.id, name=name)
        return cls(design=design)

    @classmethod
    def from_plan(cls, plan: Plan, *, name: str = "") -> "Document":
        """Adopt solver output. See `_adopt`: without it, this plan's rooms
        have no anchors and lose their names on the first wall edit."""
        return cls(design=cls._adopt(Design.single(plan, name=name)))

    @classmethod
    def from_project(cls, proj: dict) -> "Document":
        """Adopt a document the editor has been holding.

        Two things happen here that are easy to miss.

        **Faces are derived.** The editor computes rooms on the fly and
        persists only the ones a user has renamed, so an adopted project
        usually arrives with `rooms: []` even though the plan plainly has
        rooms. Waiting for the first wall edit to discover them would mean the
        first thing anyone asked the assistant -- "how big is the bedroom" --
        got answered against a document with no bedrooms in it.

        **Rooms get anchors**, so a plan drawn before identity existed becomes
        addressable by name from its first command.
        """
        design = design_from_project(proj)
        from .roomid import ensure_anchors
        from .faces import rederive_rooms
        for st in design.storeys:
            ensure_anchors(st.rooms)
            if st.walls:
                # Reconcile rather than replace: any room the user already
                # named is matched onto its face and keeps its name.
                rooms, _gone, _fresh = rederive_rooms(st)
                st.rooms = rooms
        return cls(design=cls._adopt(design))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def feed(events: Iterable[Event], *, limit: int = 40) -> str:
    """The event log as the chat shows it, and as the model reads it."""
    lines = [e.bullet() for e in events]
    if len(lines) > limit:
        lines = [f"- ... {len(lines) - limit} earlier changes"] + lines[-limit:]
    return "\n".join(lines)
