import { chromium } from 'playwright';
const BASE = process.env.BASE ?? 'http://localhost:5210';
const errors = [];
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1600, height: 950 }, deviceScaleFactor: 2 });
const pg = await ctx.newPage();
pg.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)); });
pg.on('pageerror', e => errors.push('PAGEERROR: ' + String(e).slice(0, 300)));

await pg.goto(BASE + '/editor', { waitUntil: 'networkidle' });
await pg.waitForTimeout(1500);
// dismiss the onboarding tip so it does not cover the tools
for (const label of ['Got it', 'Got it!']) {
  const btn = pg.getByRole('button', { name: label });
  if (await btn.count()) { await btn.first().click().catch(() => {}); }
}
await pg.waitForTimeout(300);

const canvas = pg.locator('canvas').first();
const box = await canvas.boundingBox();
// Draw a rectangle with the wall tool. Snap is on, so grid-aligned clicks close.
await pg.keyboard.press('w');
const ox = Math.round(box.x + 420), oy = Math.round(box.y + 260);
const W = 400, H = 300;
for (const [dx, dy] of [[0,0],[W,0],[W,H],[0,H],[0,0]]) {
  await pg.mouse.click(ox + dx, oy + dy);
  await pg.waitForTimeout(150);
}
await pg.keyboard.press('Escape');
await pg.keyboard.press('v');
await pg.waitForTimeout(1200);

const info = await pg.evaluate(() => {
  const strip = document.querySelector('.flex.items-center.gap-2.px-3');
  const rows = [...document.querySelectorAll('div')]
    .filter(d => d.className.includes('flex items-start gap-2 text-xs'))
    .map(d => d.textContent.trim().replace(/\s+/g, ' '));
  return { status: strip ? strip.textContent.trim().replace(/\s+/g, ' ') : null, rows };
});
console.log('STATUS:', info.status);
console.log('FEED:');
for (const r of info.rows) console.log('  ', r);
console.log('walls on screen:', await pg.evaluate(() =>
  document.body.textContent.match(/(\d+) walls/)?.[1] ?? '?'));
console.log('console errors:', errors.length);
for (const e of errors.slice(0, 6)) console.log('  !', e);
await pg.screenshot({ path: 'out/ui_chat_dock.png' });
await b.close();
