/**
 * The command bus.
 *
 * Every change to a design — a drag, a rename, an agent's repair — is recorded
 * here as a `Command` and sent to the service, which owns the document. The
 * store mutators still apply their change locally first, so the canvas
 * responds at pointer speed; but the answer that counts is the service's, and
 * a hash mismatch means "take theirs".
 *
 * That split is deliberate. Writing a second applier in TypeScript would mean
 * two implementations of every edit and a class of bug where the two drift
 * silently in geometry. Here the local apply is a *prediction*: if it is
 * wrong, the cost is one repaint.
 *
 * ## Units
 *
 * `Project` is float centimetres, the IR is integer millimetres. Commands are
 * always millimetres — conversion happens here, at the edge, once. `mm()` and
 * `pt()` are the only places centimetres become millimetres in this codebase.
 *
 * ## Gestures
 *
 * A drag must not become sixty commands. There is no end-of-drag hook in the
 * editor to hang one command off — `beginDrag` and `commitFurnitureMove` both
 * fire at the *start* — so instead the bus coalesces: a command carrying a
 * `coalesceKey` replaces an unsent command with the same key, and the flush is
 * debounced. Sixty pointer moves collapse into the one command that describes
 * where the thing ended up.
 *
 * This mirrors what the store already does for undo (`coalesceKeyFor`), for
 * the same reason: the interesting unit is the gesture, not the frame.
 */
import { writable, get } from 'svelte/store';
import type { Point } from '$lib/models/types';
import {
  COMMANDS,
  validateShape,
  type CommandOp,
  type CommandSource,
} from '$lib/commands/generated';

export const MM_PER_CM = 10;

/** Centimetres to millimetres, rounded to the integer the IR stores. */
export function mm(cm: number): number {
  return Math.round(cm * MM_PER_CM);
}

/** A centimetre point to a millimetre point. */
export function pt(p: Point): { x: number; y: number } {
  return { x: mm(p.x), y: mm(p.y) };
}

/** Millimetres back to centimetres, for applying a server projection. */
export function cm(millimetres: number): number {
  return millimetres / MM_PER_CM;
}

export interface Command {
  id: string;
  op: CommandOp;
  params: Record<string, unknown>;
  source: CommandSource;
  storey_id?: string;
  description?: string;
  seq?: number;
  /** Not sent to the service: used locally to collapse a gesture. */
  coalesceKey?: string;
}

export interface DesignEvent {
  seq: number;
  command_id: string;
  op: string;
  source: string;
  summary: string;
  refs: string[];
  at: string;
  /** Set locally when the service has not confirmed the command yet. */
  pending?: boolean;
}

export type BusStatus = 'local' | 'syncing' | 'synced' | 'error';

interface BusState {
  /** null until a document is adopted by the service. */
  designId: string | null;
  seq: number;
  hash: string | null;
  status: BusStatus;
  error: string | null;
  /** Commands recorded locally and not yet acknowledged. */
  pending: Command[];
}

export const busState = writable<BusState>({
  designId: null,
  seq: 0,
  hash: null,
  status: 'local',
  error: null,
  pending: [],
});

/**
 * The event feed: what the chat pane renders and what the model reads next
 * turn. Manual edits and agent edits land in the same list, in order, which is
 * the entire point — the assistant is told "the middle bedroom is now
 * 3.6 x 3.9 m" because that is what happened, not because someone remembered
 * to tell it.
 */
export const eventLog = writable<DesignEvent[]>([]);

/** What the rules engine says about the document as it stands now.
 *
 *  Findings describe the CURRENT document, not a turn, which is why they live
 *  here and not in the chat panel: a mouse drag can break a plan just as an
 *  agent edit can. The service returns them on every command reply and this
 *  store used not to exist, so all of them were dropped -- 81 hand edits that
 *  sealed a master bedroom off from the rest of the house reported nothing. */
export interface Finding {
  rule_id: string;
  severity: 'error' | 'warn' | 'info';
  detail: string;
  element_ids?: string[];
}

export const findings = writable<Finding[]>([]);

/** Events the user has not seen in the chat yet, for the unread dot. */
export const unseenEvents = writable<number>(0);

let localSeq = 0;

function uid(): string {
  // crypto.randomUUID is unavailable in older Safari and in SSR.
  const r = () => Math.random().toString(36).slice(2, 10);
  return `c-${r()}${r()}`.slice(0, 14);
}

/** The id an `add_*` command supplies for the thing it creates.
 *  Commands carry the ids they mint so replaying the log is deterministic. */
export function newId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

let transport: ((cmds: Command[]) => Promise<SyncReply>) | null = null;

export interface SyncReply {
  seq: number;
  hash: string;
  events: DesignEvent[];
  rejected: { command: Command; errors: string[] }[];
  /** Present when the client's optimistic state diverged. */
  projection?: unknown;
}

/** Install the transport. Until one is set the bus is a local recorder, which
 *  is exactly what an anonymous or offline session needs. */
export function setTransport(fn: ((cmds: Command[]) => Promise<SyncReply>) | null) {
  transport = fn;
  busState.update((s) => ({ ...s, status: fn ? 'syncing' : 'local' }));
}

/**
 * Record a command. This is the only way a change enters the log.
 *
 * Shape validation runs client-side so an obviously malformed command is
 * refused without a round trip; referential validation ("is there still a wall
 * w7") is the service's, because only the service knows.
 */
export function record(
  op: CommandOp,
  params: Record<string, unknown>,
  opts: {
    source?: CommandSource;
    storeyId?: string;
    description?: string;
    /** Commands sharing a key collapse while still unsent — one drag, one
     *  command. Use the element id and the fields being changed. */
    coalesceKey?: string;
  } = {},
): Command | null {
  const source = opts.source ?? 'user';
  const problems = validateShape(op, params, source);
  if (problems.length) {
    // A malformed command is a programming error, not a user error. Fail loud
    // in the console and drop it rather than sending nonsense to the service.
    console.error('[bus] refusing malformed command', op, problems, params);
    busState.update((s) => ({ ...s, status: 'error', error: problems[0] }));
    return null;
  }

  const state = get(busState);
  const key = opts.coalesceKey;
  const prior = key
    ? state.pending.find((c) => c.coalesceKey === key && c.op === op)
    : undefined;

  if (prior) {
    // Same gesture, later frame: keep the command id so the provisional event
    // stays in place, and overwrite the payload.
    prior.params = params;
    busState.update((s) => ({ ...s, pending: [...s.pending] }));
    eventLog.update((l) =>
      l.map((e) =>
        e.command_id === prior.id
          ? { ...e, summary: opts.description || provisionalSummary(op, params) }
          : e,
      ),
    );
    scheduleFlush();
    return prior;
  }

  const cmd: Command = {
    id: uid(),
    op,
    params,
    source,
    ...(opts.storeyId ? { storey_id: opts.storeyId } : {}),
    ...(opts.description ? { description: opts.description } : {}),
    ...(key ? { coalesceKey: key } : {}),
  };

  // Show it immediately with a provisional summary; the service's generated
  // summary replaces it on acknowledgement. Rendering nothing until the round
  // trip completes would make the user's own edit feel like it went nowhere.
  localSeq += 1;
  const provisional: DesignEvent = {
    seq: -localSeq,
    command_id: cmd.id,
    op,
    source,
    summary: opts.description || provisionalSummary(op, params),
    refs: [],
    at: new Date().toISOString(),
    pending: true,
  };
  eventLog.update((l) => [...l, provisional]);
  unseenEvents.update((n) => n + 1);
  busState.update((s) => ({ ...s, pending: [...s.pending, cmd] }));

  scheduleFlush();
  return cmd;
}

/** A readable placeholder until the service returns the real summary.
 *  Kept deliberately thin: the service generates the text that matters, from
 *  the document, and duplicating that logic here would guarantee they drift. */
function provisionalSummary(op: string, params: Record<string, unknown>): string {
  const spec = (COMMANDS as Record<string, { doc: string }>)[op];
  const words = op.replace(/_/g, ' ');
  const first = words.charAt(0).toUpperCase() + words.slice(1);
  const id = ['wall_id', 'room_id', 'opening_id', 'furniture_id', 'element_id']
    .map((k) => params[k])
    .find((v) => typeof v === 'string');
  return id ? `${first} (${id})` : spec ? first : op;
}

/** How long to let a gesture settle before sending. Long enough that a drag
 *  becomes one command, short enough that a click feels immediate. */
export const FLUSH_DEBOUNCE_MS = 150;

let flushTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleFlush(): void {
  if (!transport) return;
  if (flushTimer !== null) clearTimeout(flushTimer);
  flushTimer = setTimeout(() => {
    flushTimer = null;
    void flush();
  }, FLUSH_DEBOUNCE_MS);
}

let flushing = false;

/** Send pending commands. Serialised, because order is the log's meaning. */
export async function flush(): Promise<void> {
  if (flushing || !transport) return;
  const state = get(busState);
  if (!state.pending.length) return;

  flushing = true;
  const batch = state.pending.slice();
  try {
    const reply = await transport(
      batch.map(({ coalesceKey, ...wire }) => wire as Command),
    );
    applyReply(batch, reply);
  } catch (e) {
    // Keep the commands queued. A dead service must not lose the user's work,
    // and the log is the work.
    busState.update((s) => ({
      ...s,
      status: 'error',
      error: e instanceof Error ? e.message : String(e),
    }));
  } finally {
    flushing = false;
  }
  if (get(busState).pending.length && get(busState).status !== 'error') {
    scheduleFlush();
  }
}

function applyReply(sent: Command[], reply: SyncReply): void {
  const sentIds = new Set(sent.map((c) => c.id));
  const rejectedIds = new Set(reply.rejected.map((r) => r.command.id));

  eventLog.update((log) => {
    // Drop the provisional entries for this batch and splice in the real ones.
    const kept = log.filter((e) => !(e.pending && sentIds.has(e.command_id)));
    const confirmed = reply.events.map((e) => ({ ...e, pending: false }));
    return [...kept, ...confirmed].sort((a, b) => a.seq - b.seq);
  });

  busState.update((s) => ({
    ...s,
    seq: reply.seq,
    hash: reply.hash,
    status: 'synced',
    error: reply.rejected.length
      ? `${reply.rejected.length} change(s) could not be applied`
      : null,
    pending: s.pending.filter((c) => !sentIds.has(c.id)),
  }));

  if (rejectedIds.size) {
    for (const r of reply.rejected) {
      console.warn('[bus] rejected', r.command.op, r.errors);
    }
  }
}

/**
 * Fold in an update the service produced on its own initiative — an agent turn,
 * a re-solve, another tab.
 *
 * Without this the assistant's edits would live only in the reply text: the
 * change feed would not show them and the canvas would not draw them, which is
 * exactly the two-systems feeling the shared log exists to prevent.
 */
export function ingestServerUpdate(update: {
  seq: number;
  hash: string;
  events?: DesignEvent[];
  projection?: unknown;
  findings?: Finding[];
}): void {
  // `undefined` means "this reply carried none", which is not the same as
  // "the document is clean" -- only replace when the server actually spoke.
  if (update.findings) findings.set(update.findings);
  if (update.events?.length) {
    const incoming = update.events.map((e) => ({ ...e, pending: false }));
    eventLog.update((log) => {
      const known = new Set(log.map((e) => `${e.command_id}:${e.seq}`));
      const fresh = incoming.filter((e) => !known.has(`${e.command_id}:${e.seq}`));
      return [...log, ...fresh].sort((a, b) => a.seq - b.seq);
    });
    unseenEvents.update((n) => n + incoming.length);
  }
  busState.update((s) => ({
    ...s,
    seq: update.seq,
    hash: update.hash,
    status: 'synced',
  }));
}

/** Adopt a document the service already knows about. */
export function attach(designId: string, seq: number, hash: string,
                       events: DesignEvent[] = []): void {
  busState.set({ designId, seq, hash, status: 'synced', error: null, pending: [] });
  eventLog.set(events.map((e) => ({ ...e, pending: false })));
  unseenEvents.set(0);
}

export function markEventsSeen(): void {
  unseenEvents.set(0);
}

/** Everything recorded so far, oldest first. */
export function events(): DesignEvent[] {
  return get(eventLog);
}

/** Reset — used when switching documents. */
export function reset(): void {
  localSeq = 0;
  eventLog.set([]);
  unseenEvents.set(0);
  busState.set({ designId: null, seq: 0, hash: null, status: 'local',
                 error: null, pending: [] });
}
