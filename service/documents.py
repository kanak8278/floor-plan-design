"""Document endpoints: the stateful half of the service.

`service/app.py` is stateless by design, and its three endpoints -- generate,
validate, render -- genuinely are pure functions of their input. This module
sits alongside them and owns the part that cannot be: the document, its command
log, and the chat transcript.

Why that has to be server-side rather than posted in with each call:

  * The **log** is the history a design needs. The editor's own undo stack is
    50 in-memory entries wiped by clearing browser data.
  * The **transcript** references rooms from many edits ago, so it needs stable
    identity, which needs one authority for what the rooms are.
  * `from_project` is a **projection**. Reasoning about a document by
    converting a posted copy each time relocates the divergence into the
    conversion rather than removing it.

Storage is a process-local dict for now. Everything a database would need is
already in `Document` -- `to_dict()`, snapshots, and a verifiable log -- so
swapping in SQLite is a persistence layer, not a redesign.
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from fpeval.commands import Command, TABLE                      # noqa: E402
from fpeval.document import Document, feed                      # noqa: E402
from fpeval.rules import validate as validate_plan              # noqa: E402
from fpeval.bylaws import BENGALURU                             # noqa: E402

router = APIRouter()

# design_id -> Document. Process-local; see the module docstring.
DOCUMENTS: dict[str, Document] = {}
# design_id -> chat transcript, as Anthropic message dicts.
TRANSCRIPTS: dict[str, list[dict]] = {}

MAX_DOCUMENTS = 200         # a soft cap so a long-running dev server cannot grow forever


# --------------------------------------------------------------------------
# wire types
# --------------------------------------------------------------------------

class AdoptIn(BaseModel):
    project: dict = Field(..., description="OpenPlan3D Project JSON")
    design_id: Optional[str] = None
    # Throw away a document this service is already holding and adopt the
    # client's copy instead. Off by default: see `adopt`.
    replace: bool = False


class CommandIn(BaseModel):
    id: str = ""
    op: str
    params: dict = Field(default_factory=dict)
    source: str = "user"
    storey_id: str = ""
    description: str = ""


class CommandsIn(BaseModel):
    commands: list[CommandIn] = Field(default_factory=list)
    # The client's view of the log when it composed this batch. Not used to
    # reject -- referential validation at apply time already handles a stale
    # author -- but reported back so a divergence is visible.
    base_seq: Optional[int] = None


def _doc(design_id: str) -> Document:
    doc = DOCUMENTS.get(design_id)
    if doc is None:
        raise HTTPException(404, f"no design {design_id!r}")
    return doc


def _findings_json(design_id: str) -> list[dict[str, Any]]:
    """Validator findings for the active storey, cheap enough to send every
    time -- the rules engine runs in about 2.4 ms."""
    doc = DOCUMENTS.get(design_id)
    plan = doc.design.active if doc else None
    if plan is None or not plan.walls:
        return []
    try:
        findings = validate_plan(plan, brief=None, profile=BENGALURU)
    except Exception:
        # A validator crash must not take the edit with it.
        return []
    return [{
        "id": getattr(f, "id", "") or getattr(f, "rule_id", ""),
        "rule_id": getattr(f, "rule_id", ""),
        "severity": getattr(f, "severity", "info"),
        "message": getattr(f, "message", str(f)),
        "refs": list(getattr(f, "refs", []) or []),
    } for f in findings]


def _sync_reply(design_id: str, doc: Document, events, rejected,
                *, projection: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "design_id": design_id,
        "seq": doc.seq,
        "hash": doc.hash,
        "events": [e.to_dict() for e in events],
        "rejected": [r.to_dict() for r in rejected],
        "findings": _findings_json(design_id),
    }
    if projection:
        out["projection"] = doc.projection()
    return out


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

@router.post("/api/designs")
def adopt(body: AdoptIn) -> dict[str, Any]:
    """Take over a document the editor has been holding.

    The editor is where a design starts -- a user draws before they talk -- so
    adoption rather than creation is the normal path. Rooms get anchors on the
    way in, which is what makes them addressable by name from the first
    message.
    """
    design_id = body.design_id or str(body.project.get("id") or "").replace(
        "proj-", "") or f"d-{uuid.uuid4().hex[:10]}"

    # Re-attaching -- a page reload, a second tab -- must NOT reset the
    # document. The service is the authority; the client's copy is a
    # projection of it, and possibly a stale one. Replacing here silently
    # discarded the command log while keeping the transcript, so the assistant
    # remembered edits the document no longer had. It read as the model
    # hallucinating; it was the service losing state.
    existing = DOCUMENTS.get(design_id)
    if existing is not None and not body.replace:
        return _adopted_state(design_id, existing, reattached=True)

    try:
        doc = Document.from_project(body.project)
    except Exception as e:
        raise HTTPException(400, f"could not read that project: "
                                 f"{type(e).__name__}: {e}")
    if len(DOCUMENTS) >= MAX_DOCUMENTS:
        for stale in list(DOCUMENTS)[:len(DOCUMENTS) - MAX_DOCUMENTS + 1]:
            DOCUMENTS.pop(stale, None)
            TRANSCRIPTS.pop(stale, None)
    DOCUMENTS[design_id] = doc
    # A replaced document gets a fresh transcript: a conversation about a
    # document that no longer exists is worse than no conversation.
    TRANSCRIPTS[design_id] = [] if body.replace else TRANSCRIPTS.setdefault(
        design_id, [])
    return _adopted_state(design_id, doc, reattached=False)


def _adopted_state(design_id: str, doc: Document,
                   *, reattached: bool) -> dict[str, Any]:
    return {
        "design_id": design_id,
        "seq": doc.seq,
        "hash": doc.hash,
        "reattached": reattached,
        # On a re-attach the client may be behind, so hand back the whole log
        # and the projection rather than an empty list it would mistake for
        # "nothing has happened".
        "events": [e.to_dict() for e in doc.events] if reattached else [],
        "projection": doc.projection() if reattached else None,
        "rooms": [{"id": r.id, "name": r.name, "category": r.category,
                   "area_m2": round(r.area_m2, 2)}
                  for r in (doc.design.active.rooms if doc.design.active else [])],
        "findings": _findings_json(design_id),
    }


@router.get("/api/designs/{design_id}")
def get_design(design_id: str, since: int = 0) -> dict[str, Any]:
    doc = _doc(design_id)
    return {
        "design_id": design_id,
        "seq": doc.seq,
        "hash": doc.hash,
        "events": [e.to_dict() for e in doc.events_since(since)],
        "projection": doc.projection(),
        "findings": _findings_json(design_id),
    }


@router.post("/api/designs/{design_id}/commands")
def post_commands(design_id: str, body: CommandsIn) -> dict[str, Any]:
    """Apply a batch. Partial application is the contract, not a fallback.

    Six commands where two name a wall that no longer exists should land four
    and explain two. Failing the batch would throw away work the user watched
    happen; half-applying it silently would be worse.
    """
    doc = _doc(design_id)
    cmds = [Command(op=c.op, params=c.params, source=c.source,  # type: ignore[arg-type]
                    id=c.id or "", storey_id=c.storey_id,
                    description=c.description)
            for c in body.commands]
    events, rejected = doc.apply_batch(cmds)
    # Send the projection back only when something was refused: that is when
    # the client's optimistic state and the document can have parted company.
    return _sync_reply(design_id, doc, events, rejected,
                       projection=bool(rejected))


@router.get("/api/designs/{design_id}/events")
def get_events(design_id: str, since: int = 0) -> dict[str, Any]:
    doc = _doc(design_id)
    return {"seq": doc.seq,
            "events": [e.to_dict() for e in doc.events_since(since)],
            "feed": feed(doc.events_since(since))}


@router.post("/api/designs/{design_id}/undo")
def undo(design_id: str) -> dict[str, Any]:
    doc = _doc(design_id)
    event = doc.undo()
    return {"design_id": design_id, "seq": doc.seq, "hash": doc.hash,
            "undone": event.to_dict() if event else None,
            "projection": doc.projection(),
            "findings": _findings_json(design_id)}


@router.get("/api/designs/{design_id}/verify")
def verify(design_id: str) -> dict[str, Any]:
    """Does replaying the log reproduce the document?

    Exposed rather than kept as a test because it is the integrity check for
    the whole mechanism, and the answer depends on real user command sequences,
    not on the ones I thought to write down.
    """
    doc = _doc(design_id)
    problems = doc.verify_log()
    return {"ok": not problems, "problems": problems, "seq": doc.seq,
            "commands": len(doc.log)}


@router.get("/api/vocabulary")
def vocabulary() -> dict[str, Any]:
    from fpeval.commands import table_manifest
    return table_manifest()


@router.get("/api/designs")
def list_designs() -> dict[str, Any]:
    return {"designs": [
        {"design_id": k, "seq": v.seq, "hash": v.hash,
         "name": v.design.name,
         "storeys": len(v.design.storeys),
         "messages": len(TRANSCRIPTS.get(k, []))}
        for k, v in DOCUMENTS.items()]}


# --------------------------------------------------------------------------
# chat
# --------------------------------------------------------------------------

class ChatIn(BaseModel):
    message: str
    design_id: Optional[str] = None
    # The client's last-seen seq, so the model is told what changed since it
    # last spoke rather than being handed the whole history every turn.
    seq: int = 0
    # The editor's document, sent so a first message works without a separate
    # adopt call. Ignored once the design is known here.
    project: Optional[dict] = None


@router.post("/api/chat")
def chat(body: ChatIn) -> dict[str, Any]:
    """One user message. Edits the document, returns prose.

    The design is adopted on the fly if this is the first message, so talking
    to a plan you just drew needs no setup step.
    """
    import os

    design_id = body.design_id or ""
    if design_id not in DOCUMENTS:
        if not body.project:
            raise HTTPException(
                400, "no such design, and no project was sent to adopt")
        adopted = adopt(AdoptIn(project=body.project, design_id=design_id or None))
        design_id = adopted["design_id"]

    doc = _doc(design_id)
    transcript = TRANSCRIPTS.setdefault(design_id, [])

    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return {
            "design_id": design_id,
            "error": "No Anthropic credential is set on the design service.",
            "hint": "Export ANTHROPIC_API_KEY (or run `ant auth login`) in the "
                    "shell that starts uvicorn, then restart it.",
            "seq": doc.seq, "hash": doc.hash, "events": [], "rejected": [],
        }

    from fpeval.agent import run_turn

    t0 = time.time()
    try:
        result = run_turn(
            doc, transcript, body.message,
            last_seen_seq=body.seq,
            findings_fn=lambda: _plan_findings(design_id),
        )
    except Exception as e:                        # never lose the document
        raise HTTPException(500, f"{type(e).__name__}: {e}")

    return {
        "design_id": design_id,
        "reply": result.reply,
        "error": result.error or None,
        "seq": doc.seq,
        "hash": doc.hash,
        "events": [e.to_dict() for e in result.events],
        "rejected": [r.to_dict() for r in result.rejected],
        "findings": _findings_json(design_id),
        "steps": result.steps,
        "usage": result.usage,
        "ms": round(1000 * (time.time() - t0)),
        "projection": doc.projection() if result.events else None,
    }


def _plan_findings(design_id: str) -> list[Any]:
    """Finding objects (not JSON) for the agent's own tool."""
    doc = DOCUMENTS.get(design_id)
    plan = doc.design.active if doc else None
    if plan is None or not plan.walls:
        return []
    try:
        return list(validate_plan(plan, brief=None, profile=BENGALURU))
    except Exception:
        return []


@router.get("/api/designs/{design_id}/transcript")
def transcript(design_id: str) -> dict[str, Any]:
    """The conversation, for reloading the pane after a refresh."""
    _doc(design_id)
    out = []
    for m in TRANSCRIPTS.get(design_id, []):
        role = m.get("role")
        if role == "system":
            continue                       # operator state, not conversation
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "text": content})
            continue
        text = "".join(getattr(b, "text", "") or (
            b.get("text", "") if isinstance(b, dict) else "")
            for b in (content or []))
        if text.strip():
            out.append({"role": role, "text": text.strip()})
    return {"design_id": design_id, "messages": out}
