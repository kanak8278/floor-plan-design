<script>
  import { onMount } from 'svelte';
  let status = 'Loading…';
  let ok = false;
  let suite = [];
  let resplan = [];
  let tab = 'suite';
  let filter = 'all';

  const KEY = 'floorplan_projects';
  // Everything fpeval has ever written, including the legacy prefixes from
  // earlier runs, so a stale generation cannot survive a reload.
  const GENERATED = ['fpeval-', 'sx-', 'fp-', 'b-', 'demo-', 'proj-'];
  let purged = 0;

  onMount(async () => {
    try {
      const load = async (f) => { try { return await (await fetch(f)).json(); } catch { return []; } };
      [suite, resplan] = await Promise.all([load('/fpeval/suite.json'), load('/fpeval/projects.json')]);
      const all = JSON.parse(localStorage.getItem(KEY) || '{}');
      // PURGE every plan we have ever generated before seeding. The old code
      // merged, so each regeneration left the previous one behind and the list
      // filled with superseded duplicates under four different id prefixes.
      // Anything the user drew themselves is untouched.
      let removed = 0;
      for (const k of Object.keys(all)) {
        if (GENERATED.some((pre) => k.startsWith(pre))) { delete all[k]; removed++; }
      }
      for (const p of [...suite, ...resplan]) all[p.id] = JSON.stringify(p);
      localStorage.setItem(KEY, JSON.stringify(all));
      purged = removed;
      status = `${suite.length} generated + ${resplan.length} converted plans loaded` + (removed ? `, ${removed} superseded plan(s) purged.` : '.');
      ok = true;
    } catch (e) { status = 'Failed: ' + e; }
  });

  const shown = () => {
    const list = tab === 'suite' ? suite : resplan;
    if (tab !== 'suite' || filter === 'all') return list;
    if (filter === 'pass') return list.filter(p => p._meta?.passed);
    if (filter === 'fail') return list.filter(p => !p._meta?.passed);
    return list.filter(p => (p._meta?.features || []).includes(filter));
  };

  const featureList = () => {
    const c = {};
    for (const p of suite) for (const f of (p._meta?.features || [])) c[f] = (c[f] || 0) + 1;
    return Object.entries(c).sort((a, b) => b[1] - a[1]);
  };
</script>

<svelte:head><title>fpeval — generated + converted plans</title></svelte:head>

<main>
  <h1>fpeval plan review</h1>
  <p class="sub">Click any card to open it in the editor. Generated plans come from the prompt suite
     (envelope &rarr; CP-SAT &rarr; validator); converted plans come from ResPlan.</p>
  <div class="status" class:ok>{status}</div>

  <div class="tabs">
    <button class="danger" on:click={() => {
      const all = JSON.parse(localStorage.getItem(KEY) || '{}');
      let n = 0;
      for (const k of Object.keys(all)) {
        if (GENERATED.some((pre) => k.startsWith(pre))) { delete all[k]; n++; }
      }
      localStorage.setItem(KEY, JSON.stringify(all));
      suite = []; resplan = [];
      status = `cleared ${n} generated plan(s); anything you drew yourself is untouched`;
    }}>Clear generated</button>
    <button class:active={tab === 'suite'} on:click={() => tab = 'suite'}>Generated ({suite.length})</button>
    <button class:active={tab === 'resplan'} on:click={() => tab = 'resplan'}>ResPlan ({resplan.length})</button>
  </div>

  {#if tab === 'suite' && suite.length}
    <div class="filters">
      <button class:on={filter === 'all'} on:click={() => filter = 'all'}>all</button>
      <button class:on={filter === 'pass'} on:click={() => filter = 'pass'}>passing</button>
      <button class:on={filter === 'fail'} on:click={() => filter = 'fail'}>failing</button>
      {#each featureList() as [f, n]}
        <button class:on={filter === f} on:click={() => filter = f}>{f} <em>{n}</em></button>
      {/each}
    </div>
  {/if}

  <div class="grid">
    {#each shown() as p}
      <a class="card" href={'/editor?id=' + p.id}>
        <div class="row">
          <div class="name">{p.name}</div>
          {#if p._meta?.passed !== undefined}
            <span class="pill" class:pass={p._meta.passed} class:fail={!p._meta.passed}>
              {p._meta.passed ? 'PASS' : 'FAIL'}
            </span>
          {/if}
        </div>
        {#if p._meta?.prompt}<div class="prompt">&ldquo;{p._meta.prompt}&rdquo;</div>{/if}
        <div class="meta">
          {#if p._meta?.rooms}<span>{p._meta.rooms} rooms</span>{/if}
          {#if p._meta?.carpet_sqft}<span>{p._meta.carpet_sqft} sq ft carpet</span>{/if}
          {#if p._meta?.coverage != null}<span>{(p._meta.coverage * 100).toFixed(0)}% coverage</span>{/if}
          {#if p._meta?.errors != null}<span class:bad={p._meta.errors > 0}>{p._meta.errors} err</span>{/if}
          {#if p._meta?.warnings != null}<span>{p._meta.warnings} warn</span>{/if}
          {#if p._meta?.vastu != null}<span>vastu {p._meta.vastu}</span>{/if}
          {#if p._meta?.bedrooms}<span>{p._meta.bedrooms} BHK</span>{/if}
          {#if p._meta?.area_m2}<span>{p._meta.area_m2} m&sup2;</span>{/if}
        </div>
        {#if p._meta?.failed_checks?.length}
          <div class="fails">failed: {p._meta.failed_checks.join(', ')}</div>
        {/if}
      </a>
    {/each}
  </div>

  <footer>
    Generated plans are checked by the rules engine (33 NBC/bye-law/geometry rules + 9 weighted Vastu);
    <strong>err</strong> counts hard violations, <strong>warn</strong> includes Vastu advisories.<br>
    ResPlan dimensions are <strong>indicative</strong> &mdash; those coordinates are not metric, so scale is
    inferred from <code>wall_depth</code> assuming 226&nbsp;mm walls (&plusmn;30%).
  </footer>
</main>

<style>
  main{font:14px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:28px 32px 60px;background:#0f1115;color:#e6e8ec;min-height:100vh}
  h1{font-size:20px;margin:0 0 4px}
  .sub{color:#9aa3af;margin:0 0 18px;max-width:70ch}
  .status{padding:9px 13px;border-radius:8px;background:#1a1e26;margin-bottom:16px;color:#9aa3af}
  .status.ok{color:#4ade80}
  .tabs{display:flex;gap:8px;margin-bottom:14px}
  .tabs button{padding:7px 14px;border:1px solid #262c37;background:#151922;color:#9aa3af;border-radius:8px;cursor:pointer;font:inherit}
  .tabs button.active{background:#1e293b;color:#dbeafe;border-color:#3b82f6}
  .tabs button.danger{margin-left:auto;border-color:#7f1d1d;color:#fca5a5}
  .tabs button.danger:hover{background:#3f1414}
  .filters{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px}
  .filters button{padding:3px 9px;border:1px solid #262c37;background:#12161e;color:#8b93a1;border-radius:99px;cursor:pointer;font:12px inherit}
  .filters button.on{background:#1e293b;color:#93c5fd;border-color:#3b82f6}
  .filters em{color:#5b6472;font-style:normal}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:12px}
  a.card{display:block;padding:13px 15px;border:1px solid #262c37;border-radius:10px;background:#151922;text-decoration:none;color:#e6e8ec;transition:.12s}
  a.card:hover{border-color:#3b82f6;background:#1a2030}
  .row{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
  .name{font-weight:600;font-size:13.5px;line-height:1.35}
  .pill{flex:none;padding:1px 7px;border-radius:99px;font:11px/1.6 inherit;font-weight:600}
  .pill.pass{background:#052e16;color:#4ade80}
  .pill.fail{background:#3f1414;color:#fca5a5}
  .prompt{color:#7c8492;font-size:12px;margin:7px 0 0;font-style:italic;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .meta{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}
  .meta span{padding:1px 7px;border-radius:99px;background:#1a2030;color:#9aa3af;font-size:11.5px}
  .meta span.bad{background:#3f1414;color:#fca5a5}
  .fails{margin-top:7px;color:#fca5a5;font-size:11.5px}
  footer{margin-top:26px;color:#6b7280;font-size:12.5px;line-height:1.7}
  code{background:#1a1e26;padding:1px 5px;border-radius:4px}
</style>
