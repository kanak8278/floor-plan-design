import { chromium } from 'playwright';
const b = await chromium.launch();
const pg = await (await b.newContext()).newPage();
await pg.goto('http://localhost:5199/fpeval', {waitUntil:'domcontentloaded'});
// plant stale entries from earlier prefixes plus a user-drawn one
await pg.evaluate(() => {
  const K='floorplan_projects';
  const all = {};
  for (const id of ['sx-old1','fp-old2','b-old3','demo-old4','proj-old5','fpeval-sx-old6'])
    all[id] = JSON.stringify({id, name:'stale '+id, floors:[{id:'f',walls:[],rooms:[],doors:[],windows:[]}]});
  all['my-own-plan'] = JSON.stringify({id:'my-own-plan', name:'drawn by hand', floors:[{id:'f',walls:[],rooms:[],doors:[],windows:[]}]});
  localStorage.setItem(K, JSON.stringify(all));
});
const before = await pg.evaluate(() => Object.keys(JSON.parse(localStorage.getItem('floorplan_projects')||'{}')).length);
await pg.reload({waitUntil:'networkidle'});
await pg.waitForTimeout(2500);
const after = await pg.evaluate(() => Object.keys(JSON.parse(localStorage.getItem('floorplan_projects')||'{}')));
const mine = after.includes('my-own-plan');
const stale = after.filter(k => ['sx-old1','fp-old2','b-old3','demo-old4','proj-old5','fpeval-sx-old6'].includes(k));
console.log(`  before: ${before} keys (6 stale + 1 hand-drawn)`);
console.log(`  after : ${after.length} keys`);
console.log(`  stale survivors: ${stale.length === 0 ? 'none' : stale.join(', ')}`);
console.log(`  hand-drawn plan preserved: ${mine}`);
console.log(`  status: "${(await pg.textContent('.status')).trim()}"`);
await b.close();
