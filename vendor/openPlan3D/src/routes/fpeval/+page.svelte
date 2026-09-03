<script>
  import { onMount } from 'svelte';
  let status = 'Seeding localStorage…';
  let ok = false;
  let projects = [];
  onMount(async () => {
    const KEY = 'floorplan_projects';
    try {
      projects = await (await fetch('/fpeval/projects.json')).json();
      const all = JSON.parse(localStorage.getItem(KEY) || '{}');
      for (const p of projects) all[p.id] = JSON.stringify(p);
      localStorage.setItem(KEY, JSON.stringify(all));
      status = `Loaded ${projects.length} projects into this browser. They also appear under “Projects”.`;
      ok = true;
    } catch (e) { status = 'Failed: ' + e; }
  });
</script>

<svelte:head><title>fpeval — converted plans</title></svelte:head>

<main>
  <h1>Converted ResPlan plans</h1>
  <p class="sub">ResPlan &rarr; canonical IR (integer mm) &rarr; OpenPlan3D <code>Project</code> JSON. Click one to open it in the editor.</p>
  <div class="status" class:ok>{status}</div>
  <div class="grid">
    {#each projects as p}
      <a class="card" href={'/editor?id=' + p.id}>
        <div class="id">{p.id}</div>
        <div class="meta"><span class="badge">{p._meta.bedrooms} BHK</span><span class="badge">{p._meta.area_m2} m&sup2;</span></div>
        <div class="meta sm">{p._meta.rooms} rooms &middot; {p._meta.walls} walls &middot; {p._meta.openings} openings</div>
      </a>
    {/each}
  </div>
  <footer>
    Dimensions are <strong>indicative</strong>: ResPlan coordinates are not metric, so scale is inferred
    from <code>wall_depth</code> assuming 226&nbsp;mm walls (&plusmn;30%).<br>
    Room count may exceed the IR's by 1&ndash;2 &mdash; ResPlan has no circulation category, so unlabelled
    space is correctly detected as an extra room by <code>detectRooms</code>.
  </footer>
</main>

<style>
  main{font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:32px;background:#0f1115;color:#e6e8ec;min-height:100vh}
  h1{font-size:20px;margin:0 0 4px}
  .sub{color:#9aa3af;margin:0 0 24px}
  .status{padding:10px 14px;border-radius:8px;background:#1a1e26;margin-bottom:20px;color:#9aa3af}
  .status.ok{color:#4ade80}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
  a.card{display:block;padding:14px 16px;border:1px solid #262c37;border-radius:10px;background:#151922;text-decoration:none;color:#e6e8ec;transition:.12s}
  a.card:hover{border-color:#3b82f6;background:#1a2030}
  .id{font-weight:600;margin-bottom:6px}
  .meta{color:#9aa3af;font-size:12.5px}
  .meta.sm{margin-top:6px}
  .badge{display:inline-block;padding:1px 7px;border-radius:99px;background:#1e293b;color:#93c5fd;font-size:11.5px;margin-right:6px}
  footer{margin-top:28px;color:#6b7280;font-size:12.5px;line-height:1.7}
  code{background:#1a1e26;padding:1px 5px;border-radius:4px}
</style>
