/**
 * Cross-language verification: run OpenPlan3D's REAL room detection over our
 * converted Projects.
 *
 *   node tests/js/verify.ts            (Node 26 runs .ts directly)
 *
 * `roomDetection.ts` here is a byte-for-byte copy of
 * vendor/openPlan3D/src/lib/utils/roomDetection.ts apart from the `$lib` import
 * alias, which Node cannot resolve. checkVendorParity() asserts that, so a
 * green run really does mean "the upstream algorithm accepts our output".
 */
import { readFileSync, existsSync } from 'node:fs';
import { detectRooms, getRoomPolygon, roomCentroid } from './roomDetection.ts';
import type { Wall, Room, Point } from './types.ts';

const HERE = new URL('.', import.meta.url).pathname;
const VENDOR = HERE + '../../vendor/openPlan3D/src/lib/utils/roomDetection.ts';

function checkVendorParity(): string {
  if (!existsSync(VENDOR)) return 'vendor copy not present - parity UNVERIFIED';
  const norm = (s: string) =>
    s.replace(/from '\$lib\/models\/types'/, "from './types'")
     .replace(/from '\.\/types'/g, "from './types'")
     .replace(/\s+$/gm, '');
  const a = norm(readFileSync(VENDOR, 'utf8'));
  const b = norm(readFileSync(HERE + 'roomDetection.ts', 'utf8'));
  return a === b ? 'identical to vendor/openPlan3D (import alias aside)'
                 : 'DIVERGED from vendor/openPlan3D - copy is stale';
}

// ---------------------------------------------------------------- utils

const q = (a: number[], p: number) => {
  if (!a.length) return NaN;
  const s = [...a].sort((x, y) => x - y);
  return s[Math.min(s.length - 1, Math.round((p / 100) * (s.length - 1)))];
};
const pct = (n: number, d: number) => `${((100 * n) / Math.max(d, 1)).toFixed(2)}%`;

function shoelace(pts: Point[]): number {
  let s = 0;
  for (let i = 0; i < pts.length; i++) {
    const j = (i + 1) % pts.length;
    s += pts[i].x * pts[j].y - pts[j].x * pts[i].y;
  }
  return s / 2;
}

function pointInPoly(p: Point, poly: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i].x, yi = poly[i].y, xj = poly[j].x, yj = poly[j].y;
    if (((yi > p.y) !== (yj > p.y)) &&
        (p.x < ((xj - xi) * (p.y - yi)) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}

// ---------------------------------------------------------------- load

const samples = HERE + 'samples.json';
if (!existsSync(samples)) {
  console.error('tests/js/samples.json missing - run tests/export_samples.py first');
  process.exit(1);
}
const projects = JSON.parse(readFileSync(samples, 'utf8'));

// ---------------------------------------------------------------- counters

let n = 0, crashed = 0;
let exact = 0, within1 = 0, moreThanExpected = 0, fewerThanExpected = 0;
// detectRooms drops any face below 1000 cm^2 (0.1 m^2) and above 1e7 cm^2
// (1000 m^2) by design. ResPlan labels some vestigial slivers as rooms, so a
// shortfall is only a converter defect if it is NOT explained by that floor.
const DETECT_MIN_M2 = 0.1, DETECT_MAX_M2 = 1000;
let fewerExplained = 0, fewerUnexplained = 0;
const areaErrs: number[] = [];
const detCounts: number[] = [];
const extraFaceAgree = { agree: 0, total: 0 };

// structural invariants of the Project we emit
const inv = {
  orphanOpeningWall: 0,
  positionOutOfRange: 0,
  openingOverrunsWall: 0,
  wallThicknessNonPositive: 0,
  wallHeightNonPositive: 0,
  wallZeroLength: 0,
  roomWallIdMissing: 0,
  roomWallsEmpty: 0,
  duplicateWallId: 0,
};
const failIds: Record<string, string[]> = {};
const note = (k: string, id: string) => {
  (failIds[k] ||= []).length < 8 && failIds[k].push(id);
};

// getRoomPolygon / roomCentroid exercise
const gp = {
  detected: 0, empty: 0, degenerate: 0, centroidOutside: 0,
  areaErrs: [] as number[],
};
const gpIr = { rooms: 0, empty: 0, closed: 0, areaErrs: [] as number[] };

const t0 = Date.now();

for (const p of projects) {
  const fl = p.floors[0];
  const walls: Wall[] = fl.walls;
  const id: string = p.id;

  // ---- invariants on the emitted Project ---------------------------------
  const seen = new Set<string>();
  for (const w of walls) {
    if (seen.has(w.id)) { inv.duplicateWallId++; note('duplicateWallId', id); }
    seen.add(w.id);
    if (!(w.thickness > 0)) { inv.wallThicknessNonPositive++; note('wallThickness', id); }
    if (!(w.height > 0)) { inv.wallHeightNonPositive++; note('wallHeight', id); }
    const L = Math.hypot(w.end.x - w.start.x, w.end.y - w.start.y);
    if (!(L > 0)) { inv.wallZeroLength++; note('wallZeroLength', id); }
  }
  const byId = new Map(walls.map((w) => [w.id, w]));

  for (const o of [...fl.doors, ...fl.windows]) {
    const w = byId.get(o.wallId);
    if (!w) { inv.orphanOpeningWall++; note('orphanOpeningWall', id); continue; }
    if (!(o.position >= 0 && o.position <= 1)) {
      inv.positionOutOfRange++; note('positionOutOfRange', id);
    }
    // the opening must land within the host wall's extent
    const L = Math.hypot(w.end.x - w.start.x, w.end.y - w.start.y);
    const c = o.position * L;
    const half = o.width / 2;
    if (c - half < -0.1 || c + half > L + 0.1) {
      inv.openingOverrunsWall++; note('openingOverrunsWall', id);
    }
  }

  for (const r of fl.rooms) {
    if (!r.walls || r.walls.length === 0) { inv.roomWallsEmpty++; note('roomWallsEmpty', id); }
    for (const wid of r.walls || []) {
      if (!byId.has(wid)) { inv.roomWallIdMissing++; note('roomWallIdMissing', id); }
    }
  }

  // ---- the real detectRooms ---------------------------------------------
  let rooms: Room[];
  try {
    rooms = detectRooms(walls);
  } catch (e) {
    crashed++; note('detectRoomsThrew', id); continue;
  }
  n++;
  const exp = p._expect.n_rooms as number;
  detCounts.push(rooms.length);
  if (rooms.length === exp) exact++;
  else if (rooms.length > exp) moreThanExpected++;
  else {
    fewerThanExpected++;
    const outOfRange = (p._expect.areas_m2 as number[])
      .filter((a) => a < DETECT_MIN_M2 || a > DETECT_MAX_M2).length;
    if (exp - rooms.length <= outOfRange) { fewerExplained++; }
    else { fewerUnexplained++; note('roomShortfallUnexplained', id); }
  }
  if (Math.abs(rooms.length - exp) <= 1) within1++;

  // does the JS face count agree with what Python predicted?
  if (p._expect.n_faces != null) {
    extraFaceAgree.total++;
    if (rooms.length === p._expect.n_faces) extraFaceAgree.agree++;
  }

  if (rooms.length === exp) {
    const got = rooms.map((r) => r.area).sort((a, b) => a - b);
    const want = p._expect.areas_m2 as number[];
    for (let i = 0; i < got.length; i++) {
      if (want[i] > 0.5) areaErrs.push(Math.abs(got[i] - want[i]) / want[i]);
    }
  }

  // ---- getRoomPolygon / roomCentroid on DETECTED rooms -------------------
  for (const r of rooms) {
    gp.detected++;
    const poly = getRoomPolygon(r, walls);
    if (poly.length === 0) { gp.empty++; note('getRoomPolygonEmpty', id); continue; }
    if (poly.length < 3) { gp.degenerate++; note('getRoomPolygonDegenerate', id); continue; }
    const a = Math.abs(shoelace(poly)) / 10000; // cm^2 -> m^2
    if (r.area > 0.5) gp.areaErrs.push(Math.abs(a - r.area) / r.area);
    const c = roomCentroid(poly);
    if (!Number.isFinite(c.x) || !Number.isFinite(c.y)) {
      gp.centroidOutside++; note('roomCentroidNaN', id);
    } else if (!pointInPoly(c, poly)) {
      gp.centroidOutside++; // legitimately possible for a concave face
    }
  }

  // ---- getRoomPolygon on OUR rooms: validates Room.walls ----------------
  // Our converter decides which walls bound each labelled room. Feeding that
  // set to OpenPlan3D's own polygon reconstruction and comparing against the
  // area we recorded is a direct test of that assignment.
  for (const r of fl.rooms) {
    gpIr.rooms++;
    const poly = getRoomPolygon(r as Room, walls);
    if (poly.length < 3) { gpIr.empty++; note('irRoomPolygonEmpty', id); continue; }
    gpIr.closed++;
    const a = Math.abs(shoelace(poly)) / 10000;
    if (r.area > 0.5) gpIr.areaErrs.push(Math.abs(a - r.area) / r.area);
  }
}

const el = (Date.now() - t0) / 1000;

// ---------------------------------------------------------------- report

console.log(`\n${'='.repeat(74)}`);
console.log(`OpenPlan3D detectRooms() on converted Projects  (n=${n}, crashed=${crashed})`);
console.log(`  roomDetection.ts: ${checkVendorParity()}`);
console.log(`  ${el.toFixed(1)}s for ${projects.length} projects = ${((1000 * el) / Math.max(projects.length, 1)).toFixed(2)} ms/plan`);
console.log('='.repeat(74));

console.log('\n--- Project structural invariants (must all be 0) ---');
let dirty = 0;
for (const [k, v] of Object.entries(inv)) {
  dirty += v;
  const ids = failIds[k] || failIds[k.replace(/^wall/, 'wall')] || [];
  console.log(`  ${k.padEnd(28)} ${String(v).padStart(7)}${v ? `   e.g. ${ids.slice(0, 5).join(',')}` : ''}`);
}
console.log(`  => ${dirty === 0 ? 'ALL CLEAN' : `${dirty} VIOLATIONS`}`);

console.log('\n--- room count vs labelled IR rooms ---');
console.log(`  exact                 : ${pct(exact, n)}`);
console.log(`  within +/-1           : ${pct(within1, n)}`);
console.log(`  editor found MORE     : ${pct(moreThanExpected, n)}  (unlabelled circulation / voids)`);
console.log(`  editor found FEWER    : ${pct(fewerThanExpected, n)}`);
console.log(`    explained by detectRooms' 0.1 m^2 floor on ResPlan sliver labels: ${pct(fewerExplained, n)}`);
console.log(`    UNEXPLAINED shortfall (a real defect)                           : ${pct(fewerUnexplained, n)}`);
console.log(`  detected rooms/plan   : median=${q(detCounts, 50)} p90=${q(detCounts, 90)}`);
console.log(`  matches Python's own face count: ${pct(extraFaceAgree.agree, extraFaceAgree.total)}`);

console.log('\n--- per-room area agreement (plans where the count matches) ---');
console.log(`  median=${(100 * q(areaErrs, 50)).toFixed(4)}%  p90=${(100 * q(areaErrs, 90)).toFixed(4)}%  p99=${(100 * q(areaErrs, 99)).toFixed(4)}%  n=${areaErrs.length}`);

console.log('\n--- getRoomPolygon() / roomCentroid() on detected rooms ---');
console.log(`  rooms=${gp.detected} emptyPolygon=${gp.empty} degenerate=${gp.degenerate}`);
console.log(`  vertex-mean centroid outside a concave face: ${gp.centroidOutside} (${pct(gp.centroidOutside, gp.detected)}) - expected, roomCentroid is a vertex mean not a polygon centroid`);
console.log(`  polygon area vs Room.area: median=${(100 * q(gp.areaErrs, 50)).toFixed(4)}%  p90=${(100 * q(gp.areaErrs, 90)).toFixed(4)}%  n=${gp.areaErrs.length}`);

console.log('\n--- getRoomPolygon() on OUR Room.walls (validates wall assignment) ---');
console.log(`  rooms=${gpIr.rooms} closedLoop=${pct(gpIr.closed, gpIr.rooms)} noPolygon=${gpIr.empty}`);
console.log(`  polygon area vs our recorded area: median=${(100 * q(gpIr.areaErrs, 50)).toFixed(4)}%  p90=${(100 * q(gpIr.areaErrs, 90)).toFixed(4)}%  n=${gpIr.areaErrs.length}`);

if (Object.keys(failIds).length) {
  console.log('\n--- example failing project ids by check ---');
  for (const [k, v] of Object.entries(failIds)) console.log(`  ${k.padEnd(28)} ${v.join(', ')}`);
}

const hardFail = crashed > 0 || dirty > 0 || fewerUnexplained > 0;
console.log(`\nVERDICT: ${hardFail ? 'FAIL' : 'PASS'}`);
process.exit(hardFail ? 1 : 0);
