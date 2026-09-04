/**
 * Conformance driver: run `reconcileFaces` from the vendor tree over cases fed
 * in on stdin as JSON, and print the result as JSON on stdout.
 *
 * Exists so `tests/test_room_identity.py` can push identical faces through both
 * the TypeScript rule and its Python twin and compare. Two implementations of
 * one rule is the standing risk of having a browser and a service that both
 * need it; this is how the risk is held down.
 *
 * Coordinates arrive in centimetres, which is what `Project` uses.
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import type { Room, Point } from './types.ts';

const HERE = new URL('.', import.meta.url).pathname;
const VENDOR = `${HERE}../../vendor/openPlan3D/src/lib/utils/roomIdentity.ts`;
const GEN = `${HERE}.generated`;

mkdirSync(GEN, { recursive: true });
writeFileSync(
  `${GEN}/roomIdentity.ts`,
  readFileSync(VENDOR, 'utf8')
    .replace("from '$lib/models/types'", "from '../types.ts'")
    .replace("from '$lib/utils/roomDetection'", "from '../roomDetection.ts'"),
);
const { reconcileFaces } = await import(`${GEN}/roomIdentity.ts`);

interface CaseFace { room: Room; polygon: Point[] }
interface Case { name: string; faces: CaseFace[]; persisted: Room[] }

const cases: Case[] = JSON.parse(readFileSync(0, 'utf8'));

const out = cases.map((c) => {
  const r = reconcileFaces(c.faces, c.persisted);
  return {
    name: c.name,
    rooms: r.rooms.map((x: Room) => ({
      id: x.id,
      name: x.name ?? '',
      anchor: x.anchor ? { x: x.anchor.x, y: x.anchor.y } : null,
    })),
    unmatched: r.unmatched.map((x: Room) => x.id),
    fresh: r.fresh.map((x: Room) => x.id),
  };
});

console.log(JSON.stringify(out));
