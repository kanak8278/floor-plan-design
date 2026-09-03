import { chromium } from 'playwright';
import { readFileSync, writeFileSync } from 'node:fs';

const projects = JSON.parse(readFileSync('out/demo_projects.json','utf8'));
const BASE = 'http://localhost:5199';
const errors = [], warns = [];

const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
const pg = await ctx.newPage();
pg.on('console', m => { if (m.type()==='error') errors.push(m.text().slice(0,300));
                        if (m.type()==='warning') warns.push(m.text().slice(0,200)); });
pg.on('pageerror', e => errors.push('PAGEERROR: '+String(e).slice(0,300)));

// seed localStorage
await pg.goto(BASE, { waitUntil: 'domcontentloaded' });
await pg.evaluate(ps => {
  const all = {};
  for (const p of ps) all[p.id] = JSON.stringify(p);
  localStorage.setItem('floorplan_projects', JSON.stringify(all));
}, projects);

const id = projects[0].id;
await pg.goto(`${BASE}/editor?id=${id}`, { waitUntil: 'networkidle' });
await pg.waitForTimeout(3000);

// --- inventory every interactive control the user can actually see
const controls = await pg.evaluate(() => {
  const out = [];
  for (const el of document.querySelectorAll('button,[role=button],select,input[type=checkbox],a')) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const label = (el.getAttribute('title') || el.getAttribute('aria-label') ||
                   el.textContent || '').trim().replace(/\s+/g,' ').slice(0,48);
    if (label) out.push(`${el.tagName.toLowerCase()}: ${label}`);
  }
  return [...new Set(out)];
});
writeFileSync('out/op3d_controls.txt', controls.join('\n'));
console.log(`visible controls: ${controls.length} (written to out/op3d_controls.txt)`);

// --- does the seeded plan actually render? count canvas ink
const ink = await pg.evaluate(() => {
  const c = document.querySelector('canvas'); if (!c) return null;
  const g = c.getContext('2d'); if (!g) return 'webgl-only';
  const d = g.getImageData(0,0,c.width,c.height).data;
  let nonbg = 0; for (let i=0;i<d.length;i+=4) if (d[i+3]>0 && !(d[i]>245&&d[i+1]>245&&d[i+2]>245)) nonbg++;
  return { w:c.width, h:c.height, inkFraction: +(nonbg/(c.width*c.height)).toFixed(4) };
});
console.log('2D canvas:', JSON.stringify(ink));
await pg.screenshot({ path: 'out/shots/01-2d.png' });

// --- room labels visible in DOM or canvas-only?
const roomText = await pg.evaluate(() => document.body.innerText.match(/\b(Bedroom|Living|Kitchen|Bathroom|Balcony|Storage)\s*\d*/g)?.slice(0,12) || []);
console.log('room names found in DOM:', roomText.length ? roomText.join(', ') : '(canvas-rendered only)');

// --- 3D
const btn3d = pg.locator('button', { hasText: /^3D$/ }).first();
let three = 'not found';
if (await btn3d.count()) {
  await btn3d.click(); await pg.waitForTimeout(5000);
  three = await pg.evaluate(() => {
    const cs = [...document.querySelectorAll('canvas')];
    const gl = cs.find(c => { try { return !!(c.getContext('webgl2')||c.getContext('webgl')); } catch { return false; } });
    return gl ? `webgl canvas ${gl.width}x${gl.height}` : `no webgl (${cs.length} canvases)`;
  });
  await pg.screenshot({ path: 'out/shots/02-3d.png' });
}
console.log('3D view:', three);

// --- walk the remaining top-level toggles, screenshot each
const named = ['2D','Elevation','Print','Layers','Settings','Area','Build','Furniture'];
for (const name of named) {
  const l = pg.locator(`button:has-text("${name}")`).first();
  if (await l.count()) {
    try { await l.click({ timeout: 2500 }); await pg.waitForTimeout(1200);
          await pg.screenshot({ path: `out/shots/ui-${name.toLowerCase()}.png` });
          console.log(`  clicked "${name}" -> ok`); }
    catch(e){ console.log(`  clicked "${name}" -> FAILED ${String(e).slice(0,80)}`); }
  } else console.log(`  "${name}" -> no such button`);
}

console.log(`\nconsole errors: ${errors.length}`);
[...new Set(errors)].slice(0,12).forEach(e=>console.log('  ERR '+e));
writeFileSync('out/op3d_console.txt', [...new Set(errors)].join('\n')+'\n--- warnings ---\n'+[...new Set(warns)].join('\n'));
await b.close();
