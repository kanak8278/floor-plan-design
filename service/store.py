"""Durable storage for designs, their command logs, and their transcripts.

## Why this is its own layer

The document service held documents in a process dictionary, which meant a
restart lost every log and every conversation. That is the one respect in which
a genuinely stateless service was better, and it is answered with a database
rather than with an argument.

The interface is `DocumentStore`. `SqliteStore` is the deployable default;
`MemoryStore` exists for tests that should not touch a disk. Nothing above this
module knows which one it has, so moving to Postgres for a multi-instance
deployment is a new class here and no change anywhere else.

## Why the log is stored, not just the document

A design's history is the product, not a debugging aid: undo, "what did the
assistant change", and "why is this wall here" all read it. Commands are
deterministic and carry the ids they create, so the log plus a base snapshot
*is* the document -- `documents` rows are a materialised convenience, and
`verify_log` proves they agree.

## Schema notes

SQL stays deliberately plain -- no JSON operators, no window functions, no
`RETURNING` -- so the same statements run on Postgres. Blobs are stored as TEXT
holding JSON rather than as SQLite `JSON`, for the same reason.

Snapshots are written every `SNAPSHOT_EVERY` commands so replay does not have
to start from nothing. Without them, opening a design with 4,000 commands means
4,000 CP-SAT-free but still real applications of geometry.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from fpeval.commands import Command, Event                     # noqa: E402
from fpeval.document import Document, LogEntry, state_hash      # noqa: E402
from fpeval.ir import Design, Plan                              # noqa: E402
from fpeval.project import to_project, design_from_project      # noqa: E402

SCHEMA_VERSION = 2
DEFAULT_DB = os.environ.get("FPEVAL_DB", str(ROOT / "data" / "designs.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS designs (
  design_id   TEXT PRIMARY KEY,
  name        TEXT NOT NULL DEFAULT '',
  seq         INTEGER NOT NULL DEFAULT 0,
  hash        TEXT NOT NULL DEFAULT '',
  -- The design at seq 0. Replay starts here when there is no nearer snapshot.
  base_json   TEXT NOT NULL,
  -- The current design, materialised so opening a document is one read.
  design_json TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commands (
  design_id   TEXT NOT NULL,
  seq         INTEGER NOT NULL,
  command_id  TEXT NOT NULL,
  op          TEXT NOT NULL,
  source      TEXT NOT NULL,
  command_json TEXT NOT NULL,
  event_json  TEXT NOT NULL,
  hash_after  TEXT NOT NULL,
  at          TEXT NOT NULL,
  -- The out-of-band input a command needed, as a Project-shaped blob. Only
  -- `replace_storey` has one: a solved Plan, which cannot be recomputed
  -- during replay because CP-SAT under a time limit is not reproducible.
  -- Without it, replaying a solve refuses and the storey is LOST -- one undo
  -- after a solve emptied the plan, 13 walls to 0, and `verify_log` reported
  -- thirteen problems that were all one missing floor.
  payload_json TEXT,
  PRIMARY KEY (design_id, seq)
);

CREATE INDEX IF NOT EXISTS commands_by_design
  ON commands (design_id, seq);

CREATE TABLE IF NOT EXISTS snapshots (
  design_id   TEXT NOT NULL,
  seq         INTEGER NOT NULL,
  design_json TEXT NOT NULL,
  PRIMARY KEY (design_id, seq)
);

CREATE TABLE IF NOT EXISTS messages (
  design_id  TEXT NOT NULL,
  ord        INTEGER NOT NULL,
  role       TEXT NOT NULL,
  -- The raw Anthropic message content, so a transcript replays with its
  -- thinking blocks and tool results intact. Stripping it to text would mean
  -- the next turn cannot continue the same reasoning.
  content_json TEXT NOT NULL,
  at         TEXT NOT NULL,
  PRIMARY KEY (design_id, ord)
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class DesignRow:
    design_id: str
    name: str
    seq: int
    hash: str
    updated_at: str
    messages: int = 0


class DocumentStore(Protocol):
    """What the document service needs from storage, and nothing more."""

    def load(self, design_id: str) -> Optional[Document]: ...
    def create(self, design_id: str, doc: Document) -> None: ...
    def save_commands(self, design_id: str, doc: Document,
                      entries: Sequence[LogEntry]) -> None: ...
    def replace(self, design_id: str, doc: Document) -> None: ...
    def delete(self, design_id: str) -> None: ...
    def list(self) -> list[DesignRow]: ...
    def load_transcript(self, design_id: str) -> list[dict]: ...
    def save_transcript(self, design_id: str, messages: Sequence[dict]) -> None: ...
    def close(self) -> None: ...


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------

def _design_to_json(design: Design) -> str:
    """Store the design as its Project projection, not as the raw dataclass.

    Two reasons. The projection is the format everything else already reads and
    writes, so there is one deserialiser to keep correct rather than two. And
    `design_from_project` is exercised on every request, so a schema drift
    shows up in tests rather than only when an old row is loaded.
    """
    return json.dumps(to_project(design), separators=(",", ":"))


def _design_from_json(blob: str) -> Design:
    return design_from_project(json.loads(blob))


def _payload_to_json(payload: Any) -> Optional[str]:
    """A command's out-of-band input, as JSON, or None.

    Today that is always a solved `Plan`, stored through the same Project
    projection as the design so there is one deserialiser rather than two. The
    cost is one plan per solve -- kilobytes, and a session has a handful --
    which is the right trade against losing the floor on undo.
    """
    if payload is None:
        return None
    try:
        if isinstance(payload, Plan):
            d = Design(id="payload", storeys=[payload],
                       active_storey_id=payload.id)
            return json.dumps(to_project(d), separators=(",", ":"))
    except Exception:
        pass
    return None


def _payload_from_json(blob: Optional[str]) -> Any:
    if not blob:
        return None
    try:
        return _design_from_json(blob).active
    except Exception:
        return None


def _doc_from_rows(base_json: str, design_json: str,
                   rows: Iterable[tuple], snapshots: Iterable[tuple]) -> Document:
    doc = Document(design=_design_from_json(design_json),
                   base=_design_from_json(base_json))
    doc.log = [
        LogEntry(seq=seq,
                 command=Command.from_dict(json.loads(cmd_json)),
                 event=_event_from_json(evt_json),
                 hash_after=hash_after,
                 payload=_payload_from_json(payload_json))
        for seq, cmd_json, evt_json, hash_after, payload_json in rows
    ]
    doc.snapshots = [(seq, _design_from_json(blob)) for seq, blob in snapshots]
    return doc


def _event_from_json(blob: str) -> Event:
    d = json.loads(blob)
    return Event(seq=int(d.get("seq", 0)), command_id=d.get("command_id", ""),
                 op=d.get("op", ""), source=d.get("source", "user"),
                 summary=d.get("summary", ""), refs=list(d.get("refs") or []),
                 at=d.get("at", ""))


# --------------------------------------------------------------------------
# SQLite
# --------------------------------------------------------------------------

class SqliteStore:
    """The deployable default.

    One connection, guarded by a lock: SQLite serialises writers anyway, and a
    connection per request costs more than it saves at this size. WAL is on so
    a long read (opening a big design) does not block a write (someone else's
    drag landing).
    """

    def __init__(self, path: str = DEFAULT_DB) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            # Durability over raw speed: a design is a person's work.
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            # Read the version BEFORE the script runs. `CREATE TABLE IF NOT
            # EXISTS` cannot add a column to a table that already exists, so a
            # v1 database keeps its old `commands` shape and only an explicit
            # ALTER brings it forward. Writing the version first, as this did,
            # meant an upgrade was never detectable.
            was = None
            try:
                row = self._db.execute(
                    "SELECT value FROM schema_meta WHERE key = 'version'"
                ).fetchone()
                was = int(row[0]) if row else None
            except sqlite3.Error:
                pass                       # no schema_meta yet: a fresh file
            self._db.executescript(SCHEMA)
            self._migrate(was)
            self._db.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                ("version", str(SCHEMA_VERSION)))
            self._db.commit()

    def _migrate(self, was: Optional[int]) -> None:
        """Bring an existing file forward. Called with the lock held.

        Additive and idempotent: every step is safe to run twice, because the
        recorded version is not trustworthy on a file written by the code that
        wrote the version before running the script.
        """
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(commands)")}
        if "payload_json" not in cols:
            # v1 -> v2. Existing rows get NULL, which is honest: their solve
            # payloads were never stored and cannot be recovered.
            self._db.execute("ALTER TABLE commands ADD COLUMN payload_json TEXT")

    # ------------------------------------------------------------- designs

    def create(self, design_id: str, doc: Document) -> None:
        now = _now()
        base = _design_to_json(doc.base if doc.base is not None else doc.design)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO designs (design_id, name, seq, hash, "
                "base_json, design_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (design_id, doc.design.name or "", doc.seq, doc.hash,
                 base, _design_to_json(doc.design), now, now))
            self._db.commit()
        if doc.log:
            self.save_commands(design_id, doc, doc.log)

    def load(self, design_id: str) -> Optional[Document]:
        with self._lock:
            row = self._db.execute(
                "SELECT base_json, design_json FROM designs WHERE design_id = ?",
                (design_id,)).fetchone()
            if row is None:
                return None
            cmds = self._db.execute(
                "SELECT seq, command_json, event_json, hash_after, payload_json "
                "FROM commands WHERE design_id = ? ORDER BY seq",
                (design_id,)).fetchall()
            snaps = self._db.execute(
                "SELECT seq, design_json FROM snapshots WHERE design_id = ? "
                "ORDER BY seq", (design_id,)).fetchall()
        return _doc_from_rows(
            row["base_json"], row["design_json"],
            [(r["seq"], r["command_json"], r["event_json"], r["hash_after"],
              r["payload_json"])
             for r in cmds],
            [(r["seq"], r["design_json"]) for r in snaps])

    def save_commands(self, design_id: str, doc: Document,
                      entries: Sequence[LogEntry]) -> None:
        """Append log entries and update the materialised design, atomically.

        One transaction: a document whose `seq` says 12 while only 11 commands
        landed would replay to a different hash and fail `verify_log` for a
        reason no one could find.
        """
        if not entries:
            return
        now = _now()
        with self._lock:
            try:
                self._db.execute("BEGIN")
                self._db.executemany(
                    "INSERT OR REPLACE INTO commands (design_id, seq, command_id,"
                    " op, source, command_json, event_json, hash_after, at,"
                    " payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(design_id, e.seq, e.command.id, e.command.op,
                      e.command.source,
                      json.dumps(e.command.to_dict(), separators=(",", ":")),
                      json.dumps(e.event.to_dict(), separators=(",", ":")),
                      e.hash_after, e.event.at or now,
                      _payload_to_json(e.payload)) for e in entries])
                # Snapshots the Document decided to take, written through.
                self._db.executemany(
                    "INSERT OR REPLACE INTO snapshots (design_id, seq, design_json)"
                    " VALUES (?, ?, ?)",
                    [(design_id, seq, _design_to_json(d))
                     for seq, d in doc.snapshots])
                self._db.execute(
                    "UPDATE designs SET seq = ?, hash = ?, name = ?, "
                    "design_json = ?, updated_at = ? WHERE design_id = ?",
                    (doc.seq, doc.hash, doc.design.name or "",
                     _design_to_json(doc.design), now, design_id))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    def replace(self, design_id: str, doc: Document) -> None:
        """Overwrite a design, dropping its log **and its conversation**.

        The transcript goes because this is a different document now, and a
        conversation whose referents no longer exist is worse than none. The
        two implementations disagreed about this until a test caught it, which
        is the argument for the contract living in the docstring rather than in
        whichever caller happens to clean up afterwards.

        Only for an explicit `replace` adopt. Undo rewinds the log instead --
        see `truncate_after`.
        """
        with self._lock:
            try:
                self._db.execute("BEGIN")
                for table in ("commands", "snapshots", "messages"):
                    self._db.execute(f"DELETE FROM {table} WHERE design_id = ?",
                                     (design_id,))
                self._db.execute("DELETE FROM designs WHERE design_id = ?",
                                 (design_id,))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        self.create(design_id, doc)

    def truncate_after(self, design_id: str, seq: int, doc: Document) -> None:
        """What undo does: drop everything past `seq` and rewrite the head."""
        now = _now()
        with self._lock:
            try:
                self._db.execute("BEGIN")
                self._db.execute(
                    "DELETE FROM commands WHERE design_id = ? AND seq > ?",
                    (design_id, seq))
                self._db.execute(
                    "DELETE FROM snapshots WHERE design_id = ? AND seq > ?",
                    (design_id, seq))
                self._db.execute(
                    "UPDATE designs SET seq = ?, hash = ?, design_json = ?, "
                    "updated_at = ? WHERE design_id = ?",
                    (doc.seq, doc.hash, _design_to_json(doc.design), now,
                     design_id))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    def delete(self, design_id: str) -> None:
        with self._lock:
            try:
                self._db.execute("BEGIN")
                for table in ("commands", "snapshots", "messages", "designs"):
                    self._db.execute(f"DELETE FROM {table} WHERE design_id = ?",
                                     (design_id,))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    def list(self) -> list[DesignRow]:
        with self._lock:
            rows = self._db.execute(
                "SELECT d.design_id, d.name, d.seq, d.hash, d.updated_at, "
                "(SELECT COUNT(*) FROM messages m WHERE m.design_id = d.design_id) "
                "AS n_messages FROM designs d ORDER BY d.updated_at DESC"
            ).fetchall()
        return [DesignRow(design_id=r["design_id"], name=r["name"], seq=r["seq"],
                          hash=r["hash"], updated_at=r["updated_at"],
                          messages=r["n_messages"]) for r in rows]

    # ---------------------------------------------------------- transcripts

    def load_transcript(self, design_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT role, content_json FROM messages WHERE design_id = ? "
                "ORDER BY ord", (design_id,)).fetchall()
        return sanitise_transcript(
            [{"role": r["role"], "content": json.loads(r["content_json"])}
             for r in rows])

    def save_transcript(self, design_id: str,
                        messages: Sequence[dict]) -> None:
        """Rewrite the transcript wholesale.

        Rewriting rather than appending because a turn mutates the tail: the
        assistant's message, its tool results, and the next turn's operator
        state all arrive together, and the agent loop owns their order. At
        conversation lengths this is a few kilobytes.
        """
        now = _now()
        with self._lock:
            try:
                self._db.execute("BEGIN")
                self._db.execute("DELETE FROM messages WHERE design_id = ?",
                                 (design_id,))
                self._db.executemany(
                    "INSERT INTO messages (design_id, ord, role, content_json, at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    [(design_id, i, str(m.get("role", "user")),
                      json.dumps(_jsonable(m.get("content")),
                                 separators=(",", ":")), now)
                     for i, m in enumerate(messages)])
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._db.close()


# What the Messages API ACCEPTS as input, per block type. `model_dump()` emits
# every field the SDK object carries, and some of those are output-only: a text
# block comes back with `parsed_output`, a tool_use block with `caller` and
# `toolset_name`. Storing them is harmless; sending them back is a hard 400,
#
#     messages.2.content.0.text.parsed_output: Extra inputs are not permitted
#
# and it only bites on the SECOND turn, because turn one hands the SDK its own
# objects and turn two replays what was persisted. A whitelist rather than a
# deny-list of the three known offenders, because the failure mode here IS the
# SDK gaining a field we did not know about.
_BLOCK_INPUT_FIELDS: dict[str, frozenset[str]] = {
    "text": frozenset({"type", "text", "citations", "cache_control"}),
    "thinking": frozenset({"type", "thinking", "signature"}),
    "redacted_thinking": frozenset({"type", "data"}),
    "tool_use": frozenset({"type", "id", "name", "input", "cache_control"}),
    "tool_result": frozenset({"type", "tool_use_id", "content", "is_error",
                              "cache_control"}),
    "image": frozenset({"type", "source", "cache_control"}),
    "document": frozenset({"type", "source", "title", "context",
                           "citations", "cache_control"}),
}


def _sanitise_block(b: dict) -> dict:
    """Drop output-only fields from one content block.

    An unknown block type passes through untouched: a server-tool block we do
    not model is better sent as-is than silently emptied.
    """
    keep = _BLOCK_INPUT_FIELDS.get(b.get("type"))
    if keep is None:
        return b
    # A null `citations` is accepted but noise; an empty one is not meaningful.
    return {k: v for k, v in b.items()
            if k in keep and not (k == "citations" and not v)}


def sanitise_transcript(messages: Sequence[dict]) -> list[dict]:
    """Make a stored transcript usable as `messages` input again.

    Applied on load as well as on save, because transcripts written before the
    whitelist existed are already poisoned and would 400 forever otherwise.
    """
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = [_sanitise_block(b) if isinstance(b, dict) else b for b in c]
        out.append({**m, "content": c})
    return out


def _jsonable(content: Any) -> Any:
    """Anthropic content blocks are SDK objects, not dicts.

    A transcript has to round-trip through JSON and come back usable as
    `messages` input, including thinking blocks -- those must be replayed
    unchanged on the same model or the next turn loses the reasoning. The SDK
    objects expose `model_dump`; anything already plain passes through.

    Output-only fields are stripped here, not on the way out, so what is on
    disk is exactly what can be replayed. See `_BLOCK_INPUT_FIELDS`.
    """
    if content is None or isinstance(content, (str, int, float, bool)):
        return content
    if isinstance(content, dict):
        d = {k: _jsonable(v) for k, v in content.items()}
        return _sanitise_block(d) if "type" in d else d
    if isinstance(content, (list, tuple)):
        return [_jsonable(v) for v in content]
    for attr in ("model_dump", "dict", "to_dict"):
        fn = getattr(content, attr, None)
        if callable(fn):
            try:
                return _jsonable(fn())
            except Exception:
                pass
    return str(content)


# --------------------------------------------------------------------------
# in-memory
# --------------------------------------------------------------------------

class MemoryStore:
    """For tests, and for a deployment that genuinely wants no disk.

    Deliberately not a subclass of `SqliteStore(":memory:")`: that would make
    every test exercise the SQL path, and a test that wants to isolate the
    service from storage should be able to.
    """

    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}
        self._transcripts: dict[str, list[dict]] = {}

    def create(self, design_id: str, doc: Document) -> None:
        self._docs[design_id] = doc

    def load(self, design_id: str) -> Optional[Document]:
        return self._docs.get(design_id)

    def save_commands(self, design_id: str, doc: Document,
                      entries: Sequence[LogEntry]) -> None:
        self._docs[design_id] = doc

    def replace(self, design_id: str, doc: Document) -> None:
        self._docs[design_id] = doc
        self._transcripts.pop(design_id, None)

    def truncate_after(self, design_id: str, seq: int, doc: Document) -> None:
        self._docs[design_id] = doc

    def delete(self, design_id: str) -> None:
        self._docs.pop(design_id, None)
        self._transcripts.pop(design_id, None)

    def list(self) -> list[DesignRow]:
        return [DesignRow(design_id=k, name=v.design.name or "", seq=v.seq,
                          hash=v.hash, updated_at="",
                          messages=len(self._transcripts.get(k, [])))
                for k, v in self._docs.items()]

    def load_transcript(self, design_id: str) -> list[dict]:
        return sanitise_transcript(self._transcripts.get(design_id, []))

    def save_transcript(self, design_id: str, messages: Sequence[dict]) -> None:
        # Through the same serialiser as SQLite on purpose. Holding the SDK's
        # own objects would make this store the only one that never exercises
        # `_jsonable`, and a test against it would then pass while the
        # deployed path returned blocks the API rejects -- which is exactly
        # what happened with `parsed_output`.
        self._transcripts[design_id] = [
            {**m, "content": _jsonable(m.get("content"))} for m in messages]

    def close(self) -> None:
        self._docs.clear()


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

def open_store(url: Optional[str] = None) -> DocumentStore:
    """Pick a store from configuration.

        FPEVAL_DB=:memory:                  in-process, nothing on disk
        FPEVAL_DB=/var/lib/fpeval/app.db    SQLite (the deployable default)

    A `postgres://` URL is recognised and refused explicitly rather than
    silently falling back to SQLite -- a deployment that thinks it has Postgres
    and actually has a file on an ephemeral container disk loses data quietly.
    """
    target = url or os.environ.get("FPEVAL_DB") or DEFAULT_DB
    if target in (":memory:", "memory"):
        return MemoryStore()
    if target.startswith(("postgres://", "postgresql://")):
        raise NotImplementedError(
            "Postgres is not implemented yet. The SQL in this module is kept "
            "plain so it can be, but refusing is better than quietly writing "
            "to a local file a deployment does not know about.")
    return SqliteStore(target)
