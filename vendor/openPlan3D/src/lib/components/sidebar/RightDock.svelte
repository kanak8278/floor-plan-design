<script lang="ts">
  /**
   * One right-hand dock, VS Code style: Chat, Properties, Layers as tabs.
   *
   * Tabs rather than three columns because chat needs the full pane width to
   * be readable, and on a laptop two right-hand columns leave the canvas too
   * narrow to work in. The dock collapses to a rail so the plan can have the
   * whole window when the user wants it.
   */
  import PropertiesPanel from '$lib/components/sidebar/PropertiesPanel.svelte';
  import LayersPanel from '$lib/components/sidebar/LayersPanel.svelte';
  import ChatPanel from '$lib/components/sidebar/ChatPanel.svelte';
  import { unseenEvents } from '$lib/commands/bus';

  type Tab = 'chat' | 'properties' | 'layers';

  let { is3D = false, open = $bindable(true), tab = $bindable<Tab>('chat') }:
    { is3D?: boolean; open?: boolean; tab?: Tab } = $props();

  let unseen = $state(0);
  unseenEvents.subscribe((n) => (unseen = n));

  const TABS: { id: Tab; label: string; icon: string; only3d?: false }[] = [
    { id: 'chat', label: 'Chat', icon: '💬' },
    { id: 'properties', label: 'Properties', icon: '⚙' },
    { id: 'layers', label: 'Layers', icon: '🗂' },
  ];

  function select(next: Tab) {
    if (open && tab === next) {
      open = false;                 // clicking the active tab collapses
      return;
    }
    tab = next;
    open = true;
  }
</script>

{#if open}
  <div class="flex flex-col h-full w-[340px] shrink-0 border-l border-gray-200 bg-white">
    <!-- tab strip -->
    <div class="flex items-stretch border-b border-gray-200 shrink-0 bg-gray-50">
      {#each TABS as t}
        <button
          class="relative flex-1 px-2 py-2 text-[11px] font-medium transition-colors"
          class:text-blue-700={tab === t.id}
          class:bg-white={tab === t.id}
          class:text-gray-500={tab !== t.id}
          class:hover:text-gray-700={tab !== t.id}
          onclick={() => select(t.id)}
          aria-current={tab === t.id}
        >
          <span class="mr-1">{t.icon}</span>{t.label}
          {#if t.id === 'chat' && unseen > 0 && tab !== 'chat'}
            <span
              class="absolute top-1.5 right-2 min-w-[15px] h-[15px] rounded-full bg-blue-600 text-[9px] leading-[15px] text-white px-1"
            >{unseen > 99 ? '99+' : unseen}</span>
          {/if}
        </button>
      {/each}
      <button
        class="px-2 text-gray-400 hover:text-gray-600 text-sm"
        onclick={() => (open = false)}
        title="Collapse panel"
        aria-label="Collapse panel"
      >›</button>
    </div>

    <!-- body: each tab keeps its own scroll position by staying mounted -->
    <div class="flex-1 min-h-0 relative">
      <div class="absolute inset-0" class:hidden={tab !== 'chat'}>
        <ChatPanel />
      </div>
      <div class="absolute inset-0 overflow-y-auto" class:hidden={tab !== 'properties'}>
        <PropertiesPanel {is3D} docked />
      </div>
      <div class="absolute inset-0 overflow-y-auto" class:hidden={tab !== 'layers'}>
        <LayersPanel docked />
      </div>
    </div>
  </div>
{:else}
  <!-- collapsed rail -->
  <div class="flex flex-col items-center gap-1 py-2 w-10 shrink-0 border-l border-gray-200 bg-white">
    {#each TABS as t}
      <button
        class="relative w-8 h-8 rounded-lg text-gray-500 hover:text-blue-700 hover:bg-white transition-colors text-sm"
        onclick={() => select(t.id)}
        title={t.label}
        aria-label={t.label}
      >
        {t.icon}
        {#if t.id === 'chat' && unseen > 0}
          <span
            class="absolute -top-0.5 -right-0.5 min-w-[15px] h-[15px] rounded-full bg-blue-600 text-[9px] leading-[15px] text-white px-1"
          >{unseen > 99 ? '99+' : unseen}</span>
        {/if}
      </button>
    {/each}
  </div>
{/if}
