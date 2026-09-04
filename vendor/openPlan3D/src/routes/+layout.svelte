<script>
  import '../app.css';
  import { browser } from '$app/environment';
  import { onMount } from 'svelte';
  import { themePreference } from '$lib/stores/theme';
  import { purgeGeneratedOnce } from '$lib/purgeGenerated';
  onMount(() => {
    import('$lib/firebase');
    // Runs once per browser, whichever page you land on. The plan list lives
    // in localStorage, so this is the only place that can actually clear it.
    purgeGeneratedOnce();
  });
  let { children } = $props();
</script>

{@render children()}
