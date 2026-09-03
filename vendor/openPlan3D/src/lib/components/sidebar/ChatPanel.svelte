<script lang="ts">
  /**
   * The conversation, and the change feed, in one column.
   *
   * They are one list on purpose. A manual edit and an agent edit are the same
   * kind of thing — a command on one document — so the user sees them in the
   * order they happened, and the assistant is told "the middle bedroom is now
   * 3.6 × 3.9 m" because that is what the log says, not because someone
   * remembered to tell it.
   *
   * Colours come from the app's own utility classes (`bg-white`, `bg-gray-50`,
   * `text-gray-*`) rather than a palette of its own, so the `html.dark`
   * overrides in `app.css` carry the panel into dark mode with no extra work.
   */
  import { eventLog, busState, markEventsSeen, ingestServerUpdate,
           type DesignEvent } from '$lib/commands/bus';
  import { adoptProjection } from '$lib/commands/attach';
  import { currentProject } from '$lib/stores/project';

  interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    text: string;
    pending?: boolean;
    error?: boolean;
  }

  let messages = $state<ChatMessage[]>([]);
  let draft = $state('');
  let sending = $state(false);
  let feedEl: HTMLDivElement | null = $state(null);

  let events = $state<DesignEvent[]>([]);
  eventLog.subscribe((l) => {
    events = l;
    queueMicrotask(scrollToEnd);
  });

  let bus = $state($busState);
  busState.subscribe((s) => (bus = s));

  type Row =
    | { kind: 'event'; key: string; event: DesignEvent }
    | { kind: 'message'; key: string; message: ChatMessage };

  // Events first, then messages: the log is the spine and the conversation
  // hangs off the end of it. When the agent path lands, replies will be pinned
  // to the seq they were answering.
  let rows = $derived<Row[]>([
    ...events.map((e) => ({
      kind: 'event' as const,
      key: `e:${e.command_id}:${e.seq}`,
      event: e,
    })),
    ...messages.map((m) => ({
      kind: 'message' as const,
      key: `m:${m.id}`,
      message: m,
    })),
  ]);

  function scrollToEnd() {
    if (feedEl) feedEl.scrollTop = feedEl.scrollHeight;
  }

  const SOURCE_STYLE: Record<string, { dot: string; label: string }> = {
    user: { dot: 'bg-sky-500', label: 'you' },
    agent: { dot: 'bg-violet-500', label: 'assistant' },
    solver: { dot: 'bg-emerald-500', label: 'solver' },
    import: { dot: 'bg-amber-500', label: 'import' },
  };

  function sourceStyle(s: string) {
    return SOURCE_STYLE[s] ?? { dot: 'bg-gray-400', label: s };
  }

  const SERVICE_HINT =
    'The design service is not reachable. Start it with:\n\n' +
    'uv run --with fastapi --with uvicorn --with shapely --with numpy \\\n' +
    '  --with ortools --with anthropic \\\n' +
    '  uvicorn service.app:app --port 8099';

  async function send() {
    const text = draft.trim();
    if (!text || sending) return;
    draft = '';
    const id = `m-${Math.random().toString(36).slice(2, 9)}`;
    const replyId = `${id}-r`;
    messages = [
      ...messages,
      { id, role: 'user', text },
      { id: replyId, role: 'assistant', text: '', pending: true },
    ];
    sending = true;
    queueMicrotask(scrollToEnd);

    const settle = (patch: Partial<ChatMessage>) => {
      messages = messages.map((m) =>
        m.id === replyId ? { ...m, pending: false, ...patch } : m,
      );
    };

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          message: text,
          design_id: bus.designId,
          seq: bus.seq,
          project: $currentProject,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        settle({
          error: true,
          text: body.hint ? `${body.error}\n\n${body.hint}` : body.error ?? SERVICE_HINT,
        });
      } else {
        settle({ text: body.reply ?? 'No reply.' });
        // The assistant's edits go into the same feed the user's do, and the
        // canvas takes the service's projection — otherwise the reply would
        // describe changes nobody can see.
        ingestServerUpdate({
          seq: body.seq,
          hash: body.hash,
          events: body.events,
        });
        if (body.projection) adoptProjection(body.projection);
      }
    } catch {
      settle({ error: true, text: SERVICE_HINT });
    } finally {
      sending = false;
      queueMicrotask(scrollToEnd);
    }
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  $effect(() => {
    markEventsSeen();
  });
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
      {#if bus.status === 'synced'}
        synced · seq {bus.seq}
      {:else if bus.status === 'local'}
        local only — service not attached
      {:else if bus.status === 'syncing'}
        syncing{bus.pending.length ? ` · ${bus.pending.length} queued` : ''}
      {:else}
        {bus.error ?? 'error'}
      {/if}
    </span>
    {#if bus.hash}
      <span class="ml-auto font-mono text-gray-400 shrink-0" title="document hash">
        {bus.hash.slice(0, 8)}
      </span>
    {/if}
  </div>

  <!-- the feed -->
  <div bind:this={feedEl} class="flex-1 overflow-y-auto px-3 py-3 space-y-2">
    {#if !rows.length}
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
        <p class="mt-4 text-[11px] text-gray-400">
          Draw a wall or rename a room and watch it appear below.
        </p>
      </div>
    {/if}

    {#each rows as row (row.key)}
      {#if row.kind === 'event'}
        {@const s = sourceStyle(row.event.source)}
        <div
          class="flex items-start gap-2 text-xs leading-snug"
          class:opacity-50={row.event.pending}
          title={`${row.event.op}${row.event.pending ? ' — not yet confirmed by the service' : ''}`}
        >
          <span class="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 {s.dot}"></span>
          <span class="text-gray-700 flex-1">{row.event.summary}</span>
          <span class="text-gray-400 shrink-0">{s.label}</span>
        </div>
      {:else}
        {@const m = row.message}
        {#if m.role === 'user'}
          <div class="flex justify-end pt-2">
            <div class="max-w-[85%] rounded-2xl rounded-br-sm bg-blue-600 px-3 py-2 text-[13px] text-white whitespace-pre-wrap">
              {m.text}
            </div>
          </div>
        {:else}
          <div class="pt-1 pb-2">
            {#if m.pending}
              <div class="flex items-center gap-1.5 text-gray-400 text-xs">
                <span class="w-1 h-1 rounded-full bg-gray-400 animate-pulse"></span>
                <span class="w-1 h-1 rounded-full bg-gray-400 animate-pulse [animation-delay:150ms]"></span>
                <span class="w-1 h-1 rounded-full bg-gray-400 animate-pulse [animation-delay:300ms]"></span>
                <span class="ml-1">thinking</span>
              </div>
            {:else}
              <div
                class="text-[13px] leading-relaxed whitespace-pre-wrap font-normal"
                class:text-rose-600={m.error}
                class:text-gray-700={!m.error}
                class:font-mono={m.error}
                class:text-[11px]={m.error}
              >{m.text}</div>
            {/if}
          </div>
        {/if}
      {/if}
    {/each}
  </div>

  <!-- composer -->
  <div class="border-t border-gray-100 p-2 shrink-0">
    <div class="flex items-end gap-2 rounded-xl bg-gray-100 px-2 py-1.5 focus-within:ring-2 focus-within:ring-blue-500/40">
      <textarea
        bind:value={draft}
        onkeydown={onKeydown}
        rows="1"
        placeholder="Ask for a change…"
        class="flex-1 resize-none bg-transparent text-[13px] text-gray-800 placeholder:text-gray-400 outline-none max-h-32 py-1"
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
