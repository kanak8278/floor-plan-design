import { chromium } from 'playwright';
import { readFileSync, writeFileSync } from 'node:fs';
const projects = JSON.parse(readFileSync('out/batch_projects.json','utf8'));
const BASE='http://localhost:5199';
const b = await chromium.launch();
const ctx = await b.newContext({ viewport:{width:1400,height:900} });
const pg = await ctx.newPage();
const errs=[]; pg.on('console',m=>{if(m.type()==='error')errs.push(m.text().slice(0,200))});
pg.on('pageerror',e=>errs.push('PAGEERROR: '+String(e).slice(0,200)));

await pg.goto(BASE,{waitUntil:'domcontentloaded'});
await pg.evaluate(ps=>{const a={};for(const p of ps)a[p.id]=JSON.stringify(p);
  localStorage.setItem('floorplan_projects',JSON.stringify(a));},projects);

const rows=[]; let blank=0, mismatch=0, threeFail=0;
for (const p of projects) {
  const before = errs.length;
  await pg.goto(`${BASE}/editor?id=${p.id}`,{waitUntil:'networkidle'});
  await pg.waitForTimeout(1400);
  const ink = await pg.evaluate(()=>{
    const c=document.querySelector('canvas'); if(!c) return 0;
    const g=c.getContext('2d'); if(!g) return -1;
    const d=g.getImageData(0,0,c.width,c.height).data; let n=0;
    for(let i=0;i<d.length;i+=4) if(d[i+3]>0&&!(d[i]>245&&d[i+1]>245&&d[i+2]>245)) n++;
    return +(n/(c.width*c.height)).toFixed(4);
  });
  // status bar: "N rooms  X m²  N walls  N doors  N windows"
  const status = await pg.evaluate(()=>document.body.innerText.replace(/\s+/g,' '));
  const g = (re)=>{const m=status.match(re); return m?Number(m[1]):null;};
  const got = { rooms:g(/(\d+) rooms/), walls:g(/(\d+) walls/),
                doors:g(/(\d+) doors/), windows:g(/(\d+) windows/) };
  const e = p._expect;
  const wallsOK = got.walls===e.walls, dOK = got.doors===e.doors, wOK = got.windows===e.windows;
  if (ink !== -1 && ink < 0.02) blank++;
  if (!wallsOK || !dOK || !wOK) mismatch++;
  rows.push({id:p.id, ink, exp_rooms:e.rooms, got_rooms:got.rooms,
             walls:`${e.walls}/${got.walls}`, doors:`${e.doors}/${got.doors}`,
             wins:`${e.windows}/${got.windows}`, newErrs: errs.length-before});
}
// 3D check on a sample of 8
for (const p of projects.slice(0,8)) {
  await pg.goto(`${BASE}/editor?id=${p.id}`,{waitUntil:'networkidle'});
  await pg.waitForTimeout(900);
  const t = pg.locator('button',{hasText:/^3D$/}).first();
  if(!await t.count()){threeFail++;continue;}
  await t.click(); await pg.waitForTimeout(2600);
  const ok = await pg.evaluate(()=>{
    for(const c of document.querySelectorAll('canvas')){
      try{ const gl=c.getContext('webgl2')||c.getContext('webgl');
        if(gl){ const px=new Uint8Array(4); gl.readPixels(c.width>>1,c.height>>1,1,1,gl.RGBA,gl.UNSIGNED_BYTE,px);
          return c.width>100 && c.height>100; } }catch{}
    } return false;
  });
  if(!ok) threeFail++;
}
writeFileSync('out/batch_render_report.json', JSON.stringify(rows,null,1));
const roomExact = rows.filter(r=>r.exp_rooms===r.got_rooms).length;
console.log(`=== batch render: ${rows.length} converted ResPlan projects in real OpenPlan3D ===`);
console.log(`  blank 2D canvases      : ${blank}`);
console.log(`  wall/door/window count mismatches: ${mismatch}`);
console.log(`  room count exactly as IR: ${roomExact}/${rows.length} (${(100*roomExact/rows.length).toFixed(1)}%)`);
console.log(`  3D render failures      : ${threeFail}/8 sampled`);
console.log(`  total console errors    : ${errs.length}`);
[...new Set(errs)].slice(0,6).forEach(e=>console.log('   ERR '+e));
console.log('\n  worst room-count gaps:');
rows.filter(r=>r.exp_rooms!==r.got_rooms).sort((a,b)=>Math.abs(b.got_rooms-b.exp_rooms)-Math.abs(a.got_rooms-a.exp_rooms))
  .slice(0,6).forEach(r=>console.log(`   ${r.id}: IR ${r.exp_rooms} rooms -> editor detected ${r.got_rooms}  (walls ${r.walls})`));
await b.close();
