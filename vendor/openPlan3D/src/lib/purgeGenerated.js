/**
 * One-shot removal of everything fpeval has ever written into localStorage.
 *
 * Why this exists at all: the plan list lives in the BROWSER, not on the
 * server. Emptying `static/fpeval/*.json` stops new plans being seeded but
 * cannot remove the ones already stored, and restarting the dev server does
 * nothing either -- localStorage belongs to the origin, not to the process.
 * The only code that can delete them is code running in the page, so it runs
 * here, on app load, rather than behind a button on a page you have to find.
 *
 * One-shot on purpose, guarded by a marker key. Purging on every load would
 * delete plans generated after this point, which is the opposite of useful.
 * Bump the marker to force a fresh sweep.
 */
const KEY = 'floorplan_projects';
const THUMB = 'floorplan_thumb_';
const MARKER = 'fpeval_purge_v1';

// Every id prefix the exporters have used: `build_gallery` writes
// `fpeval-rp-*`, `build_suite_gallery` writes `fpeval-sx-*`, `batch_export`
// writes `fpeval-b-*`, and `project.to_project` writes `proj-*`. The bare
// `sx-`/`fp-`/`b-`/`demo-` entries are from earlier runs that wrote ids
// without the `fpeval-` stem.
const GENERATED = ['fpeval-', 'sx-', 'fp-', 'b-', 'demo-', 'proj-'];

/** @returns {number} how many plans were removed */
export function purgeGeneratedOnce() {
  try {
    if (localStorage.getItem(MARKER)) return 0;
    const all = JSON.parse(localStorage.getItem(KEY) || '{}');
    let n = 0;
    for (const id of Object.keys(all)) {
      if (!GENERATED.some((pre) => id.startsWith(pre))) continue;
      delete all[id];
      try { localStorage.removeItem(THUMB + id); } catch { /* quota-only */ }
      n++;
    }
    localStorage.setItem(KEY, JSON.stringify(all));
    localStorage.setItem(MARKER, new Date().toISOString());
    if (n) {
      console.info(
        `[fpeval] removed ${n} generated plan(s) from this browser; ` +
        `${Object.keys(all).length} left. Anything you drew yourself is untouched.`);
    }
    return n;
  } catch (e) {
    console.warn('[fpeval] purge skipped:', e);
    return 0;
  }
}
