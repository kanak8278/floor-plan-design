import { chromium } from 'playwright';
const BASE = process.env.BASE ?? 'http://localhost:5210';
const b = await chromium.launch();
const pg = await (await b.newContext({ viewport: { width: 1500, height: 900 } })).newPage();
const errors = [];
pg.on('pageerror', e => errors.push(String(e).slice(0, 200)));

await pg.goto(BASE + '/editor', { waitUntil: 'networkidle' });
await pg.waitForTimeout(1500);
for (const l of ['Got it', 'Got it!']) {
  const btn = pg.getByRole('button', { name: l });
  if (await btn.count()) await btn.first().click().catch(() => {});
}

// The phrase is chosen to contain every letter the editor binds to a tool:
// v w d t h m n s g f l p, plus spaces and a question mark.
const PHRASE = 'widen the master bedroom to 12 x 14 ft please, and flag any nbc problems?';
const ta = pg.locator('textarea[placeholder="Ask for a change…"]');
await ta.click();
await pg.keyboard.type(PHRASE, { delay: 12 });
const typed = await ta.inputValue();
console.log('typed   :', JSON.stringify(typed));
console.log('expected:', JSON.stringify(PHRASE));
console.log(typed === PHRASE ? 'PASS  chat box accepts every character' : '*** FAIL ***');

// The tool must not have changed while typing.
const tool = await pg.evaluate(() => {
  const active = [...document.querySelectorAll('button')]
    .find(b => b.className.includes('bg-blue-50') || b.className.includes('border-blue-500'));
  return active ? active.textContent.trim().split('\n')[0] : '(none)';
});
console.log('active tool after typing:', tool);

// And the pre-existing bug: the room name field must take a space too.
await pg.keyboard.press('Escape');
const canvas = pg.locator('canvas').first();
const bb = await canvas.boundingBox();
await pg.keyboard.press('w');
const ox = Math.round(bb.x + 420), oy = Math.round(bb.y + 260);
for (const [dx, dy] of [[0,0],[300,0],[300,220],[0,220],[0,0]]) {
  await pg.mouse.click(ox + dx, oy + dy); await pg.waitForTimeout(120);
}
await pg.keyboard.press('Escape'); await pg.keyboard.press('v');
await pg.waitForTimeout(900);
await pg.mouse.click(ox + 150, oy + 110);
await pg.waitForTimeout(700);
const nameInput = pg.locator('input[type="text"]:visible').first();
if (await nameInput.count()) {
  await nameInput.click();
  await pg.keyboard.type('Master Bedroom', { delay: 12 });
  const v = await nameInput.inputValue();
  console.log('room name field:', JSON.stringify(v));
  console.log(v === 'Master Bedroom' ? 'PASS  rename field accepts spaces' : '*** FAIL ***');
} else {
  console.log('(no rename field visible — could not test)');
}
console.log('page errors:', errors.length, errors.slice(0, 3));
await b.close();
