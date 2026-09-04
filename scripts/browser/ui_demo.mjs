import { chromium } from 'playwright';
const BASE = process.env.BASE ?? 'http://localhost:5210';
const errors = [];
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1680, height: 1000 }, deviceScaleFactor: 2 });
const pg = await ctx.newPage();
pg.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 250)); });
pg.on('pageerror', e => errors.push('PAGEERROR: ' + String(e).slice(0, 250)));

const w = (id, x1, y1, x2, y2) => ({ id, start:{x:x1,y:y1}, end:{x:x2,y:y2}, thickness:23, height:280, color:'#444444' });
const project = {
  id: 'uidemo5', name: 'Chat demo — 9 x 7 m',
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

await pg.goto(`${BASE}/editor?id=uidemo5`, { waitUntil: 'networkidle' });
await pg.waitForTimeout(2000);
for (const l of ['Got it', 'Got it!']) {
  const btn = pg.getByRole('button', { name: l });
  if (await btn.count()) await btn.first().click().catch(() => {});
}
await pg.keyboard.press('f');
await pg.waitForTimeout(700);

const ta = pg.locator('textarea[placeholder="Ask for a change…"]');
await ta.click();
await pg.keyboard.type(
  'This is a 9 x 7 m ground floor split in two. Call the left room the master bedroom '
  + 'and the right one the living room, put a door between them, and tell me what the '
  + 'code checks say.', { delay: 4 });
await pg.keyboard.press('Enter');
console.log('sent; waiting for the reply…');
await pg.waitForFunction(() => !document.body.textContent.includes('thinking'),
  { timeout: 240000 }).catch(() => console.log('(timed out)'));
await pg.waitForTimeout(2000);

const info = await pg.evaluate(() => {
  const rows = [...document.querySelectorAll('div')]
    .filter(d => d.className.includes('flex items-start gap-2 text-xs'))
    .map(d => d.textContent.trim().replace(/\s+/g, ' '));
  const strip = [...document.querySelectorAll('span')].map(s => s.textContent.trim())
    .find(t => t.startsWith('synced') || t.startsWith('local only'));
  const reply = [...document.querySelectorAll('div')]
    .filter(d => d.className.includes('text-[13px] leading-relaxed'))
    .map(d => d.textContent.trim()).join('\n---\n');
  return { rows, strip, reply };
});
console.log('STATUS:', info.strip);
console.log('FEED:');
for (const r of info.rows) console.log('  •', r);
console.log('\nREPLY:\n' + info.reply);
console.log('\nconsole errors:', errors.length);
for (const e of errors.slice(0, 5)) console.log('  !', e);
await pg.screenshot({ path: 'out/ui_demo.png' });
// Crop the plan so the room labels are legible: they are drawn on the canvas,
// so the only way to check them is to look.
await pg.screenshot({ path: 'out/ui_plan.png',
                      clip: { x: 300, y: 55, width: 1300, height: 700 } });
// And prove the labels survive a reload: the service hands back its own
// projection, which is where the names live.
await pg.reload({ waitUntil: 'networkidle' });
await pg.waitForTimeout(2500);
await pg.keyboard.press('f');
await pg.waitForTimeout(800);
await pg.screenshot({ path: 'out/ui_plan_after_reload.png',
                      clip: { x: 300, y: 55, width: 1300, height: 700 } });
const after = await pg.evaluate(() => {
  const rows = [...document.querySelectorAll('div')]
    .filter(d => d.className.includes('flex items-start gap-2 text-xs'))
    .map(d => d.textContent.trim().replace(/\s+/g, ' '));
  const strip = [...document.querySelectorAll('span')].map(s => s.textContent.trim())
    .find(t => t.startsWith('synced') || t.startsWith('local only'));
  return { rows, strip };
});
console.log('\nAFTER RELOAD status:', after.strip);
console.log('AFTER RELOAD feed:');
for (const r of after.rows) console.log('  •', r);
await b.close();
