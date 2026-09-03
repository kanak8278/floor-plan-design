/**
 * Room identity has to survive geometry edits.
 *
 *   node tests/js/roomIdentity.test.ts
 *
 * The module under test is loaded from `vendor/openPlan3D` with its `$lib`
 * import aliases rewritten, so there is no second copy to go stale — unlike
 * `roomDetection.ts` here, which is a manual copy guarded by a parity check.
 *
 * The last test is the important one: it asserts the OLD rule (match by exact
 * wall-id set) genuinely fails the wall-split case. Without it, the other
 * tests could pass against a no-op and nobody would know.
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { detectRooms } from './roomDetection.ts';
import type { Wall, Room, Point } from './types.ts';

const HERE = new URL('.', import.meta.url).pathname;
const VENDOR = `${HERE}../../vendor/openPlan3D/src/lib/utils/roomIdentity.ts`;
const GEN = `${HERE}.generated`;

// Rewrite the vendor module's aliases and import it, so the test always runs
// against the real source rather than a copy of it.
mkdirSync(GEN, { recursive: true });
const rewritten = readFileSync(VENDOR, 'utf8')
  .replace("from '$lib/models/types'", "from '../types.ts'")
  .replace("from '$lib/utils/roomDetection'", "from '../roomDetection.ts'");
writeFileSync(`${GEN}/roomIdentity.ts`, rewritten);
const { reconcileRooms, pointInPolygon, mintRoomId } =
  await import(`${GEN}/roomIdentity.ts`);

// ------------------------------------------------------------------ harness

let passed = 0;
const failures: string[] = [];

function check(name: string, fn: () => void) {
  try {
    fn();
    passed++;
    console.log(`  ok   ${name}`);
  } catch (e) {
    failures.push(`${name}: ${(e as Error).message}`);
    console.log(`  FAIL ${name}\n       ${(e as Error).message}`);
  }
}

function eq(actual: unknown, expected: unknown, what: string) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) throw new Error(`${what}: ${a} !== ${b}`);
}

function assert(cond: boolean, what: string) {
  if (!cond) throw new Error(what);
}

// ------------------------------------------------------------------ fixtures

const W = (id: string, x1: number, y1: number, x2: number, y2: number): Wall =>
  ({ id, start: { x: x1, y: y1 }, end: { x: x2, y: y2 }, thickness: 15, height: 280, color: '#444' });

/** A 400 x 300 cm box: one room, four walls. */
function box(): Wall[] {
  return [
    W('w0', 0, 0, 400, 0),
    W('w1', 400, 0, 400, 300),
    W('w2', 400, 300, 0, 300),
    W('w3', 0, 300, 0, 0),
  ];
}

/** The same box with the bottom wall split in two — what `splitWall` does. */
function boxWithSplitBottom(): Wall[] {
  return [
    W('w0a', 0, 0, 200, 0),
    W('w0b', 200, 0, 400, 0),
    W('w1', 400, 0, 400, 300),
    W('w2', 400, 300, 0, 300),
    W('w3', 0, 300, 0, 0),
  ];
}

/** The box with a wall down the middle: two rooms where there was one. */
function boxDivided(): Wall[] {
  return [...box(), W('w4', 200, 0, 200, 300)];
}

/** Name a detected room the way a user does, which records its anchor. */
function named(rooms: Room[], index: number, name: string): Room {
  const r = rooms[index];
  return { ...r, name };
}

/** The rule the editor used before: match on the exact wall-id set. */
function reconcileByWallSet(detected: Room[], persisted: Room[]): Room[] {
  return detected.map((nr) => {
    const set = new Set(nr.walls);
    const match = persisted.find(
      (p) => p.walls.length === set.size && p.walls.every((w) => set.has(w)),
    );
    return match ? { ...nr, id: match.id, name: match.name } : nr;
  });
}

// ------------------------------------------------------------------ tests

console.log('room identity');

check('a named room keeps its name and id when a wall is split', () => {
  const before = detectRooms(box());
  eq(before.length, 1, 'one room before');
  const master = named(reconcileRooms(before, [], box()).rooms, 0, 'Master Bedroom');
  assert(!!master.anchor, 'naming a room records an anchor');

  const walls = boxWithSplitBottom();
  const after = reconcileRooms(detectRooms(walls), [master], walls);
  eq(after.rooms.length, 1, 'still one room after the split');
  eq(after.rooms[0].name, 'Master Bedroom', 'name survives');
  eq(after.rooms[0].id, master.id, 'id survives');
  eq(after.unmatched.length, 0, 'nothing orphaned');
  // The wall set genuinely changed — that is what used to break identity.
  eq(after.rooms[0].walls.includes('w0a'), true, 'walls updated to the split pair');
});

check('the wall set is refreshed even though identity is not', () => {
  const walls = box();
  const master = named(reconcileRooms(detectRooms(walls), [], walls).rooms, 0, 'Hall');
  const split = boxWithSplitBottom();
  const after = reconcileRooms(detectRooms(split), [master], split);
  assert(!after.rooms[0].walls.includes('w0'), 'stale wall id dropped');
  assert(after.rooms[0].area > 0, 'area recomputed from the current face');
});

check('dividing a room keeps identity on the side holding the anchor', () => {
  const walls = box();
  const whole = named(reconcileRooms(detectRooms(walls), [], walls).rooms, 0, 'Living');
  // Anchor sits at the centroid (200, 150), which lands on the new wall, so
  // pin it left of centre to make the expectation unambiguous.
  const left = { ...whole, anchor: { x: 100, y: 150 } as Point };

  const divided = boxDivided();
  const after = reconcileRooms(detectRooms(divided), [left], divided);
  eq(after.rooms.length, 2, 'two rooms now');
  const keeper = after.rooms.filter((r) => r.name === 'Living');
  eq(keeper.length, 1, 'exactly one room inherits the name');
  eq(keeper[0].id, left.id, 'and it keeps the id');
  eq(after.fresh.length, 1, 'the other half is reported as new');
  assert(after.fresh[0].name !== 'Living', 'the new half is not a name clone');
});

check('merging two rooms reports the loser instead of silently dropping it', () => {
  const divided = boxDivided();
  const both = reconcileRooms(detectRooms(divided), [], divided).rooms;
  eq(both.length, 2, 'two rooms to start');
  const a = { ...both[0], name: 'Bedroom 1' };
  const b = { ...both[1], name: 'Bedroom 2' };

  const walls = box();                        // dividing wall deleted
  const after = reconcileRooms(detectRooms(walls), [a, b], walls);
  eq(after.rooms.length, 1, 'one room after the merge');
  eq(after.unmatched.length, 1, 'the other name is reported, not vanished');
  const kept = after.rooms[0].name;
  assert(kept === 'Bedroom 1' || kept === 'Bedroom 2', `kept a real name, got ${kept}`);
  assert(after.unmatched[0].name !== kept, 'the reported one is the other');
});

check('ids are deterministic across passes', () => {
  const walls = box();
  const a = reconcileRooms(detectRooms(walls), [], walls).rooms[0];
  const b = reconcileRooms(detectRooms(walls), [], walls).rooms[0];
  eq(a.id, b.id, 'same geometry, same id');
  assert(!/\d{13}/.test(a.id), `no timestamp in the id: ${a.id}`);
});

check('a room saved before anchors existed still matches by wall set', () => {
  const walls = box();
  const detected = detectRooms(walls);
  const legacy: Room = {
    id: 'room-1-1717171717171',
    name: 'Kitchen',
    walls: [...detected[0].walls],
    floorTexture: 'ceramic-gray',
    area: detected[0].area,
  };                                          // note: no anchor
  const after = reconcileRooms(detectRooms(walls), [legacy], walls);
  eq(after.rooms[0].name, 'Kitchen', 'legacy name recovered');
  eq(after.rooms[0].id, legacy.id, 'legacy id kept');
  assert(!!after.rooms[0].anchor, 'and it gets an anchor for next time');
});

check('an open wall graph does not destroy names', () => {
  const walls = box();
  const master = named(reconcileRooms(detectRooms(walls), [], walls).rooms, 0, 'Master');
  const open = walls.filter((w) => w.id !== 'w2');   // mid-drag: loop broken
  const after = reconcileRooms(detectRooms(open), [master], open);
  eq(after.rooms.length, 0, 'no faces while the loop is open');
  eq(after.unmatched.length, 1, 'the name is held, not deleted');
  eq(after.unmatched[0].name, 'Master', 'and it is the right one');
});

check('pointInPolygon agrees with the obvious cases', () => {
  const sq = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }];
  eq(pointInPolygon({ x: 5, y: 5 }, sq), true, 'centre is inside');
  eq(pointInPolygon({ x: 15, y: 5 }, sq), false, 'right of it is outside');
  eq(pointInPolygon({ x: -1, y: 5 }, sq), false, 'left of it is outside');
  eq(pointInPolygon({ x: 5, y: 5 }, sq.slice(0, 2)), false, 'a line is not a polygon');
});

check('mintRoomId is stable and position-dependent', () => {
  eq(mintRoomId({ x: 100, y: 200 }), mintRoomId({ x: 100, y: 200 }), 'stable');
  assert(mintRoomId({ x: 100, y: 200 }) !== mintRoomId({ x: 101, y: 200 }),
    'distinct positions give distinct ids');
});

// The discriminating test: the rule we replaced must actually fail here.
check('REGRESSION GUARD: the old wall-set rule loses the name on a split', () => {
  const walls = box();
  const master = named(reconcileRooms(detectRooms(walls), [], walls).rooms, 0, 'Master Bedroom');
  const split = boxWithSplitBottom();
  const old = reconcileByWallSet(detectRooms(split), [master]);
  assert(old[0].name !== 'Master Bedroom',
    'the old rule should NOT survive a split — if it does, this suite proves nothing');
});

// ------------------------------------------------------------------ report

console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length) {
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
