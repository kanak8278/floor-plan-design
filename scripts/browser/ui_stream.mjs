import { chromium } from 'playwright';
const BASE = process.env.BASE ?? 'http://localhost:5210';
const ID = process.env.ID ?? 'stream1';
const errors = [];
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1680, height: 1000 }, deviceScaleFactor: 2 });
const pg = await ctx.newPage();
pg.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 250)); });
pg.on('pageerror', e => errors.push('PAGEERROR: ' + String(e).slice(0, 250)));

const w = (id, x1, y1, x2, y2) => ({ id, start:{x:x1,y:y1}, end:{x:x2,y:y2}, thickness:23, height:280, color:'#444444' });
const project = {
  id: ID, name: 'Streaming demo — 9 x 7 m',
  floors: [{ id: 'fl1', name: 'Ground Floor', level: 0,
    walls: [w('w0',0,0,900,0), w('w1',900,0,900,700), w('w2',900,700,0,700),
            w('w3',0,700,0,0), w('w4',480,0,480,700)],
    rooms: [], doors: [], windows: [], furniture: [], stairs: [], columns: [],
    guides: [], measurements: [], annotations: [], textAnnotations: [], groups: [] }],
  activeFloorId: 'fl1',
  createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
};
await pg.goto(BASE, { waitUntil: 'domcontentloaded' });
await pg.evaluate((p) => {
  const all = JSON.parse(localStorage.getItem('floorplan_projects') || '{}');
  all[p.id] = JSON.stringify(p);
  localStorage.setItem('floorplan_projects', JSON.stringify(all));
}, project);
await pg.goto(`${BASE}/editor?id=${ID}`, { waitUntil: 'networkidle' });
await pg.waitForTimeout(2000);
for (const l of ['Got it', 'Got it!']) {
  const btn = pg.getByRole('button', { name: l });
  if (await btn.count()) await btn.first().click().catch(() => {});
}
await pg.keyboard.press('f');
await pg.waitForTimeout(600);

const ta = pg.locator('textarea[placeholder="Ask for a change…"]');
await ta.click();
await pg.keyboard.type(
  'Name the left room the master bedroom and the right one the living room, '
  + 'put a door between them, and tell me what the code checks say.', { delay: 3 });

// Sample the strip while the turn runs, to prove it updates in place.
const seen = new Set();
const sampler = setInterval(async () => {
  try {
    const s = await pg.evaluate(() => {
      const btn = [...document.querySelectorAll('button[aria-expanded]')].pop();
      return btn ? btn.textContent.trim().replace(/\s+/g, ' ') : null;
    });
    if (s) seen.add(s);
  } catch {}
}, 400);

await pg.keyboard.press('Enter');
await pg.waitForFunction(
  () => { const b = [...document.querySelectorAll('button[aria-expanded]')].pop();
          return b && /Worked for/.test(b.textContent); },
  { timeout: 240000 }).catch(() => console.log('(timed out)'));
clearInterval(sampler);
await pg.waitForTimeout(1200);

console.log('STRIP STATES OBSERVED WHILE RUNNING:');
for (const s of seen) console.log('   ', s);

// Expand and read the timeline.
const strip = pg.locator('button[aria-expanded]').last();
await strip.click();
await pg.waitForTimeout(500);
const detail = await pg.evaluate(() => {
  const rail = [...document.querySelectorAll('div.border-l')]
    .map(d => d.textContent.trim().replace(/\s+/g, ' ')).filter(Boolean);
  const reply = [...document.querySelectorAll('div')]
    .filter(d => d.className.includes('text-[13px] leading-relaxed'))
    .map(d => d.textContent.trim());
  return { rail, reply };
});
console.log('\nEXPANDED TIMELINE + CHANGES:');
for (const r of detail.rail) console.log('   ', r.slice(0, 200));
console.log('\nREPLY:\n' + detail.reply.join('\n'));
console.log('\nconsole errors:', errors.length);
for (const e of errors.slice(0, 5)) console.log('  !', e);
await pg.screenshot({ path: 'out/ui_stream.png' });
await pg.screenshot({ path: 'out/ui_stream_pane.png',
                      clip: { x: 1290, y: 55, width: 390, height: 945 } });
await b.close();
