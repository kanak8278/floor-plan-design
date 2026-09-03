<script lang="ts">
  /**
   * The conversation, the work, and the change log — three planes, one column.
   *
   * The design problem is that an agent turn produces far more rows than a
   * chat message: reasoning, several tool calls, their results, the edits, and
   * finally some prose. Rendering all of that as chat bubbles gives you a wall
   * of noise. So the rows are deliberately not all the same kind of thing:
   *
   *   **Messages** — prose. The conversation. Read like text; only the user's
   *   turn gets a bubble, because long assistant answers read worse in one.
   *
   *   **Change events** — "Master bedroom target area 150–190 sqft". The
   *   durable record, one line each, and never collapsed: this is the audit
   *   trail that makes the assistant trustworthy.
   *
   *   **Activity** — thinking and read-only tool calls. Ephemeral process,
   *   collapsed into a single summary strip per turn. Live, the strip updates
   *   in place ("Reading the plan…"), so a long turn does not grow the log.
   *
   * One thing is deliberately never rendered: the `apply_commands` tool call.
   * Its effects are already change events, and showing the call as well shows
   * the same edit twice — and it is the tool the agent reaches for most, so
   * dropping the duplicate removes most of the volume for free. The service
   * does not even emit a row for it (`agent.stream_turn`).
   *
   * Colours come from the app's own utility classes so the `html.dark`
   * overrides in `app.css` carry the panel into dark mode unchanged.
   */
  import { eventLog, busState, markEventsSeen, ingestServerUpdate,
           type DesignEvent } from '$lib/commands/bus';
  import { adoptProjection } from '$lib/commands/attach';
  import { currentProject } from '$lib/stores/project';

  interface Activity {
    kind: 'thinking' | 'tool';
    label: string;
    detail?: string;
    seconds?: number;
    running?: boolean;
    text?: string;              // the reasoning summary, for thinking rows
  }

  /** One assistant turn: what was asked, what it did, what it said. */
  interface Turn {
    id: string;
    ask: string;
    activity: Activity[];
    events: DesignEvent[];
    rejected: { op: string; reason: string }[];
    reply: string;
    status: 'running' | 'done' | 'error';
    error?: string;
    seconds?: number;
    expanded: boolean;
    /** What the strip says while the turn is in flight. */
    progress: string;
  }

  let turns = $state<Turn[]>([]);
  let draft = $state('');
  let sending = $state(false);
  let feedEl: HTMLDivElement | null = $state(null);

  /** Events not attributable to a turn — the user's own edits. */
  let ownEvents = $state<DesignEvent[]>([]);
  let claimed = new Set<string>();

  eventLog.subscribe((log) => {
    ownEvents = log.filter((e) => !claimed.has(`${e.command_id}`));
    queueMicrotask(scrollToEnd);
  });

  let bus = $state($busState);
  busState.subscribe((s) => (bus = s));

  function scrollToEnd() {
    if (feedEl) feedEl.scrollTop = feedEl.scrollHeight;
  }

  const SOURCE = {
    user: { dot: 'bg-sky-500', label: 'you' },
    agent: { dot: 'bg-violet-500', label: 'assistant' },
    solver: { dot: 'bg-emerald-500', label: 'solver' },
    import: { dot: 'bg-amber-500', label: 'import' },
  } as Record<string, { dot: string; label: string }>;

  const src = (s: string) => SOURCE[s] ?? { dot: 'bg-gray-400', label: s };

  const SERVICE_HINT =
    'The design service is not reachable. Start it with:\n\n' +
    'uv run --with fastapi --with uvicorn --with shapely --with numpy \\\n' +
    '  --with ortools --with anthropic \\\n' +
    '  uvicorn service.app:app --port 8100';

  /** A short phrase for the strip while the turn runs. */
  function progressOf(t: Turn): string {
    const last = t.activity[t.activity.length - 1];
    if (!last) return 'Thinking…';
    if (last.running) {
      return last.kind === 'thinking' ? 'Thinking…' : `${last.label}…`;
    }
    if (t.events.length) return `Applying ${t.events.length} change${t.events.length === 1 ? '' : 's'}…`;
    return 'Working…';
  }

  /** What the strip says once the turn is finished: counts, not steps. */
  function summaryOf(t: Turn): string {
    const bits: string[] = [];
    if (t.seconds) bits.push(`Worked for ${t.seconds}s`);
    const reads = t.activity.filter((a) => a.kind === 'tool').length;
    if (reads) bits.push(`${reads} read${reads === 1 ? '' : 's'}`);
    if (t.events.length) {
      bits.push(`${t.events.length} change${t.events.length === 1 ? '' : 's'}`);
    }
    if (t.rejected.length) bits.push(`${t.rejected.length} refused`);
    return bits.join(' · ') || 'No changes';
  }

  async function send() {
    const text = draft.trim();
    if (!text || sending) return;
    draft = '';
    const turn: Turn = {
      id: `t-${Math.random().toString(36).slice(2, 9)}`,
      ask: text, activity: [], events: [], rejected: [], reply: '',
      status: 'running', expanded: false, progress: 'Thinking…',
    };
    turns = [...turns, turn];
    sending = true;
    queueMicrotask(scrollToEnd);

    const patch = (fn: (t: Turn) => void) => {
      turns = turns.map((x) => {
        if (x.id !== turn.id) return x;
        const next = { ...x, activity: [...x.activity], events: [...x.events],
                       rejected: [...x.rejected] };
        fn(next);
        next.progress = progressOf(next);
        return next;
      });
    };

    try {
      const res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          message: text,
          design_id: bus.designId,
          seq: bus.seq,
          project: $currentProject,
        }),
      });
      if (!res.ok || !res.body) {
        const body = await res.json().catch(() => ({}));
        patch((t) => { t.status = 'error';
                       t.error = body.detail ?? body.error ?? SERVICE_HINT; });
        return;
      }
      await readStream(res.body, patch);
    } catch {
      patch((t) => { t.status = 'error'; t.error = SERVICE_HINT; });
    } finally {
      sending = false;
      queueMicrotask(scrollToEnd);
    }
  }

  /** Server-sent events over a POST response body. `EventSource` cannot POST,
   *  and the turn needs a request body, so the framing is parsed here. */
  async function readStream(body: ReadableStream<Uint8Array>,
                            patch: (fn: (t: Turn) => void) => void) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split('\n\n');
      buffer = frames.pop() ?? '';
      for (const frame of frames) {
        const line = frame.split('\n').find((l) => l.startsWith('data: '));
        if (!line) continue;
        let ev: Record<string, any>;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }
        handle(ev, patch);
        queueMicrotask(scrollToEnd);
      }
    }
  }

  function handle(ev: Record<string, any>,
                  patch: (fn: (t: Turn) => void) => void) {
    switch (ev.type) {
      case 'thinking':
        patch((t) => {
          const last = t.activity[t.activity.length - 1];
          if (last?.kind === 'thinking' && last.running) {
            t.activity[t.activity.length - 1] =
              { ...last, text: (last.text ?? '') + ev.delta };
          } else {
            t.activity.push({ kind: 'thinking', label: 'Thinking',
                              running: true, text: ev.delta });
          }
        });
        break;
      case 'thinking_end':
        patch((t) => {
          const i = t.activity.findLastIndex(
            (a) => a.kind === 'thinking' && a.running);
          if (i >= 0) {
            t.activity[i] = { ...t.activity[i], running: false,
                              seconds: ev.seconds,
                              label: `Thought for ${ev.seconds}s` };
          }
        });
        break;
      case 'tool':
        patch((t) => {
          if (ev.state === 'start') {
            t.activity.push({ kind: 'tool', label: ev.label, running: true });
          } else {
            const i = t.activity.findLastIndex(
              (a) => a.kind === 'tool' && a.running && a.label === ev.label);
            if (i >= 0) {
              t.activity[i] = { ...t.activity[i], running: false,
                                detail: ev.detail };
            }
          }
        });
        break;
      case 'change':
        // Claimed so the flat feed does not also show it: an assistant edit
        // belongs under the turn that caused it, not floating on its own.
        claimed.add(ev.event.command_id);
        patch((t) => t.events.push({ ...ev.event, pending: false }));
        ingestServerUpdate({ seq: ev.event.seq, hash: bus.hash ?? '',
                             events: [] });
        break;
      case 'rejected':
        patch((t) => t.rejected.push({ op: ev.op, reason: ev.reason }));
        break;
      case 'text':
        patch((t) => { t.reply += ev.delta; });
        break;
      case 'done':
        patch((t) => { t.status = 'done'; t.seconds = ev.seconds; });
        ingestServerUpdate({ seq: ev.seq, hash: ev.hash, events: [] });
        if (ev.projection) adoptProjection(ev.projection);
        break;
      case 'error':
        patch((t) => { t.status = 'error'; t.error = ev.message; });
        break;
    }
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  function toggle(id: string) {
    turns = turns.map((t) => (t.id === id ? { ...t, expanded: !t.expanded } : t));
  }

  $effect(() => { markEventsSeen(); });
</script>

<div class="flex flex-col h-full bg-white">
  <!-- status strip: the honest state of the shared document -->
  <div class="flex items-center gap-2 px-3 py-1.5 text-[11px] border-b border-gray-100 shrink-0">
    <span
      class="w-1.5 h-1.5 rounded-full shrink-0"
      class:bg-emerald-500={bus.status === 'synced'}
      class:bg-amber-500={bus.status === 'syncing' || bus.status === 'local'}
      class:bg-rose-500={bus.status === 'error'}
    ></span>
    <span class="text-gray-500 truncate">
      {#if bus.status === 'synced'}synced · seq {bus.seq}
      {:else if bus.status === 'local'}local only — service not attached
      {:else if bus.status === 'syncing'}syncing{bus.pending.length ? ` · ${bus.pending.length} queued` : ''}
      {:else}{bus.error ?? 'error'}{/if}
    </span>
    {#if bus.hash}
      <span class="ml-auto font-mono text-gray-400 shrink-0" title="document hash">
        {bus.hash.slice(0, 8)}
      </span>
    {/if}
  </div>

  <div bind:this={feedEl} class="flex-1 overflow-y-auto px-3 py-3">
    {#if !ownEvents.length && !turns.length}
      <div class="text-xs leading-relaxed py-4">
        <p class="mb-2 text-gray-600 font-medium">
          Ask for a change, or make one yourself.
        </p>
        <p class="text-gray-500">
          Either way it goes through the same document, and shows up here as a
          line you can point at.
        </p>
        <ul class="mt-3 space-y-1.5 text-gray-400">
          <li>“Widen the master bedroom to 12 × 14”</li>
          <li>“Move the pooja room to the north-east”</li>
          <li>“Why did the validator complain about the kitchen?”</li>
        </ul>
      </div>
    {/if}

    <!-- the user's own edits: flush left, one line each, never collapsed -->
    {#each ownEvents as e (e.command_id + e.seq)}
      {@const s = src(e.source)}
      <div class="flex items-start gap-2 text-xs leading-snug py-[2px]"
           class:opacity-50={e.pending}
           title={`${e.op}${e.pending ? ' — not yet confirmed' : ''}`}>
        <span class="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 {s.dot}"></span>
        <span class="text-gray-700 flex-1">{e.summary}</span>
        <span class="text-gray-400 shrink-0">{s.label}</span>
      </div>
    {/each}

    {#each turns as t (t.id)}
      <!-- the ask -->
      <div class="flex justify-end pt-3">
        <div class="max-w-[85%] rounded-2xl rounded-br-sm bg-blue-600 px-3 py-2
                    text-[13px] leading-snug text-white whitespace-pre-wrap">
          {t.ask}
        </div>
      </div>

      <!-- the work: one strip, expandable -->
      <button
        class="mt-2 w-full flex items-center gap-1.5 text-left text-[11px]
               text-gray-400 hover:text-gray-600 transition-colors"
        onclick={() => toggle(t.id)}
        aria-expanded={t.expanded}
      >
        <span class="w-3 shrink-0 text-center">{t.expanded ? '⌄' : '›'}</span>
        {#if t.status === 'running'}
          <span class="flex gap-[3px] items-center shrink-0" aria-hidden="true">
            <span class="w-1 h-1 rounded-full bg-gray-400 animate-pulse"></span>
            <span class="w-1 h-1 rounded-full bg-gray-400 animate-pulse [animation-delay:150ms]"></span>
            <span class="w-1 h-1 rounded-full bg-gray-400 animate-pulse [animation-delay:300ms]"></span>
          </span>
          <span class="truncate">{t.progress}</span>
        {:else}
          <span class="truncate">{summaryOf(t)}</span>
        {/if}
      </button>

      {#if t.expanded}
        <!-- the timeline, only when asked for -->
        <div class="ml-3 pl-3 border-l border-gray-200 mt-1 mb-1 space-y-1">
          {#each t.activity as a}
            <div class="text-[11px] text-gray-400">
              <div class="flex items-baseline gap-2">
                <span class="flex-1">{a.label}</span>
                {#if a.detail}<span class="text-gray-300">{a.detail}</span>{/if}
              </div>
              {#if a.kind === 'thinking' && a.text}
                <p class="mt-0.5 mb-1 text-gray-400 leading-relaxed whitespace-pre-wrap">
                  {a.text}
                </p>
              {/if}
            </div>
          {/each}
          {#if !t.activity.length}
            <div class="text-[11px] text-gray-300">No tool calls.</div>
          {/if}
        </div>
      {/if}

      <!-- what it changed: nested under the turn, so cause sits with effect -->
      {#if t.events.length}
        <div class="ml-3 pl-3 border-l-2 border-violet-200 mt-1 space-y-[2px]">
          {#each t.events as e (e.command_id + e.seq)}
            <div class="flex items-start gap-2 text-xs leading-snug" title={e.op}>
              <span class="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 bg-violet-500"></span>
              <span class="text-gray-700 flex-1">{e.summary}</span>
            </div>
          {/each}
        </div>
      {/if}

      {#if t.rejected.length}
        <div class="ml-3 pl-3 border-l-2 border-amber-200 mt-1 space-y-[2px]">
          {#each t.rejected as r}
            <div class="text-xs leading-snug text-amber-700">
              <span class="font-medium">{r.op}</span> refused — {r.reason}
            </div>
          {/each}
        </div>
      {/if}

      <!-- the answer -->
      {#if t.reply}
        <div class="mt-2 text-[13px] leading-relaxed text-gray-700 whitespace-pre-wrap">
          {t.reply}
        </div>
      {/if}
      {#if t.status === 'error' && t.error}
        <div class="mt-2 text-[11px] leading-relaxed text-rose-600 font-mono whitespace-pre-wrap">
          {t.error}
        </div>
      {/if}
    {/each}
  </div>

  <div class="border-t border-gray-100 p-2 shrink-0">
    <div class="flex items-end gap-2 rounded-xl bg-gray-100 px-2 py-1.5
                focus-within:ring-2 focus-within:ring-blue-500/40">
      <textarea
        bind:value={draft}
        onkeydown={onKeydown}
        rows="1"
        placeholder="Ask for a change…"
        class="flex-1 resize-none bg-transparent text-[13px] text-gray-800
               placeholder:text-gray-400 outline-none max-h-32 py-1"
      ></textarea>
      <button
        class="shrink-0 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors"
        class:bg-blue-600={draft.trim() && !sending}
        class:text-white={draft.trim() && !sending}
        class:bg-gray-200={!draft.trim() || sending}
        class:text-gray-400={!draft.trim() || sending}
        disabled={!draft.trim() || sending}
        onclick={send}
        aria-label="Send"
      >Send</button>
    </div>
    <p class="mt-1 px-1 text-[10px] text-gray-400">
      Enter to send · Shift+Enter for a new line
    </p>
  </div>
</div>
