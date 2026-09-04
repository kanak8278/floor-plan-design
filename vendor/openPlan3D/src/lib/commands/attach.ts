/**
 * Attach the editor's document to the design service.
 *
 * Called once when a project loads. The service adopts the document, the bus
 * gets a transport, and from then on every command the store records is
 * applied by the one applier and comes back with a generated summary.
 *
 * Failure here is not fatal and must not be. If the service is down the bus
 * stays a local recorder: the editor works exactly as it did before, the
 * change feed still fills up with provisional summaries, and the chat says
 * plainly that it cannot reach the service. Losing the ability to draw because
 * a Python process is not running would be a worse product than not having the
 * chat at all.
 */
import { get } from 'svelte/store';
import {
  attach, setTransport, reset, busState,
  type Command, type SyncReply, type DesignEvent,
} from '$lib/commands/bus';
import { currentProject } from '$lib/stores/project';
import type { Project } from '$lib/models/types';

export interface AttachResult {
  ok: boolean;
  designId?: string;
  error?: string;
  rooms?: { id: string; name: string; category: string; area_m2: number }[];
}

async function postJson(url: string, body: unknown): Promise<Response> {
  return fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** Hand the current project to the service and wire the bus to it. */
export async function attachDocument(project: Project): Promise<AttachResult> {
  reset();
  try {
    const res = await postJson('/api/designs', {
      project,
      design_id: project.id,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const error = body.hint ?? body.detail ?? body.error ?? `HTTP ${res.status}`;
      busState.update((s) => ({ ...s, status: 'local', error: String(error) }));
      return { ok: false, error: String(error) };
    }
    const body = await res.json();
    const designId: string = body.design_id;

    setTransport(async (cmds: Command[]): Promise<SyncReply> => {
      const r = await postJson(`/api/designs/${designId}/commands`, {
        commands: cmds,
        base_seq: get(busState).seq,
      });
      if (!r.ok) throw new Error(`service returned HTTP ${r.status}`);
      const reply = await r.json();
      // The service sends a projection only when something was refused, i.e.
      // when the optimistic state and the document can have parted company.
      if (reply.projection) adoptProjection(reply.projection);
      return {
        seq: reply.seq,
        hash: reply.hash,
        events: (reply.events ?? []) as DesignEvent[],
        rejected: reply.rejected ?? [],
      };
    });

    // A re-attach (reload, second tab) hands back the whole log and the
    // service's projection, because the client's copy may be behind.
    attach(designId, body.seq ?? 0, body.hash ?? '',
           (body.events ?? []) as DesignEvent[]);
    if (body.reattached && body.projection) adoptProjection(body.projection);
    return { ok: true, designId, rooms: body.rooms };
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    setTransport(null);
    busState.update((s) => ({ ...s, status: 'local', error }));
    return { ok: false, error };
  }
}

/**
 * Replace local state with the service's projection.
 *
 * Only called when the two have diverged. The canvas repaints and any
 * in-flight selection may be dropped, which is the whole cost of the client
 * having guessed wrong — cheap enough that guessing is worth it.
 */
export function adoptProjection(projection: unknown): void {
  const proj = projection as Project & { _fpeval_doc?: unknown };
  const current = get(currentProject);
  if (!current) return;
  // Dates arrive as strings over JSON; the store expects Date objects.
  const revived: Project = {
    ...proj,
    createdAt: new Date(proj.createdAt as unknown as string),
    updatedAt: new Date(proj.updatedAt as unknown as string),
  };
  currentProject.set(revived);
}

/** Pull anything that happened while this tab was not the one editing. */
export async function refreshEvents(designId: string, since: number)
  : Promise<DesignEvent[]> {
  const r = await fetch(`/api/designs/${designId}/events?since=${since}`);
  if (!r.ok) return [];
  const body = await r.json();
  return (body.events ?? []) as DesignEvent[];
}
