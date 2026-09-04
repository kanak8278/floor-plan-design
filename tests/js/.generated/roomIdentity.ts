/**
 * Stable room identity.
 *
 * `detectRooms()` derives rooms from cycles in the wall graph and mints ids as
 * `room-${n}-${Date.now()}`, so every re-detection produces different ids. The
 * editor recovered a room's name by matching its **exact wall-id set**, which
 * fails the moment the wall set changes at all: split a wall, add one, or drag
 * one so a T-junction appears, and "Master Bedroom" comes back as "Room 3".
 *
 * That is survivable when a human is looking at the screen. It is not
 * survivable for a conversation, where the entire vocabulary is room names and
 * a message from ten edits ago still has to mean something.
 *
 * So identity moves off the wall set and onto an **anchor**: a point inside the
 * room, recorded the first time the room acquires a name or any other property
 * worth keeping. Whichever detected face contains that point *is* that room.
 * Splitting a wall does not move the point. Neither does adding a window,
 * thickening a wall, or nudging a wall a few centimetres. Only a change that
 * genuinely re-partitions the space moves a room across an anchor -- which is
 * exactly when identity *should* change.
 *
 * Rooms that match nothing are left alone rather than deleted: mid-drag the
 * wall graph is briefly open and every face vanishes, and a name must not be
 * destroyed by a gesture that is still in progress.
 */
import type { Wall, Room, Point } from '../types.ts';
import { getRoomPolygon, roomCentroid } from '../roomDetection.ts';

export interface ReconcileResult {
  /** The detected faces, wearing the identity of whatever they matched. */
  rooms: Room[];
  /** Persisted rooms that no face claimed this pass. Not deleted — see above. */
  unmatched: Room[];
  /** Faces that matched no persisted room, so they are genuinely new. */
  fresh: Room[];
}

/** Ray casting. Points exactly on an edge count as inside, which is what we
 *  want for an anchor that was placed on a centroid of a degenerate sliver. */
export function pointInPolygon(p: Point, poly: Point[]): boolean {
  if (poly.length < 3) return false;
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i];
    const b = poly[j];
    const straddles = a.y > p.y !== b.y > p.y;
    if (!straddles) continue;
    const x = ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x;
    if (p.x < x) inside = !inside;
  }
  return inside;
}

function polygonArea(poly: Point[]): number {
  let sum = 0;
  for (let i = 0; i < poly.length; i++) {
    const j = (i + 1) % poly.length;
    sum += poly[i].x * poly[j].y - poly[j].x * poly[i].y;
  }
  return Math.abs(sum / 2);
}

function sameWallSet(a: string[] | undefined, b: string[] | undefined): boolean {
  if (!a || !b || a.length !== b.length) return false;
  const s = new Set(a);
  return b.every((w) => s.has(w));
}

/**
 * A short id derived from geometry, so re-detecting the same face twice in a
 * row gives the same id. `Date.now()` in an id makes every diff noise.
 *
 * The hash is taken over **millimetres**, not over the centimetres this
 * function receives. The id has to be unit-independent: `src/fpeval/roomid.py`
 * mints ids for the same face from the integer-millimetre IR, and if the two
 * disagreed the optimistic client and the authoritative service would end up
 * with different names for the room the user just created. The conformance
 * test in `tests/test_room_identity.py` is what caught that.
 */
export function mintRoomId(centroid: Point): string {
  let h = 0x811c9dc5;
  const key = `${Math.round(centroid.x * 10)}|${Math.round(centroid.y * 10)}`;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `room-${h.toString(36)}`;
}

export interface Face {
  room: Room;
  polygon: Point[];
}

interface Sized {
  room: Room;
  polygon: Point[];
  centroid: Point;
  area: number;
}

/**
 * Give each newly detected face the identity of the persisted room it is.
 *
 * `persisted` is the authoritative list (a floor's saved rooms, plus whatever
 * was on screen last pass); `detected` is this pass's output from
 * `detectRooms()`. Later entries in `persisted` win ties, so callers should
 * pass saved rooms last.
 */
export function reconcileRooms(
  detected: Room[],
  persisted: Room[],
  walls: Wall[],
): ReconcileResult {
  return reconcileFaces(
    detected.map((room) => ({ room, polygon: getRoomPolygon(room, walls) })),
    persisted,
  );
}

/**
 * The rule itself, over faces whose polygons are already known.
 *
 * Split out from `reconcileRooms` so the Python twin in `src/fpeval/roomid.py`
 * can be fed byte-identical input: polygon *recovery* is a separate problem
 * with its own measurements, and conflating the two would mean a conformance
 * test could fail for reasons that have nothing to do with identity.
 */
export function reconcileFaces(
  input: Face[],
  persisted: Room[],
): ReconcileResult {
  const faces: Sized[] = input.map(({ room, polygon }) => ({
    room,
    polygon,
    centroid: polygon.length >= 3 ? roomCentroid(polygon) : { x: 0, y: 0 },
    area: polygon.length >= 3 ? polygonArea(polygon) : 0,
  }));

  const claimedBy = new Map<number, Room>();   // face index -> persisted room
  const matched = new Set<string>();           // persisted room ids

  // Pass 1: anchors. A persisted room owns the face containing its anchor.
  // When two anchors land in one face (the user deleted a dividing wall), the
  // room whose recorded area is closest to the face's wins; the other is
  // reported unmatched rather than silently merged.
  for (const room of persisted) {
    const anchor = room.anchor;
    if (!anchor) continue;
    let best = -1;
    let bestGap = Infinity;
    for (let i = 0; i < faces.length; i++) {
      if (!pointInPolygon(anchor, faces[i].polygon)) continue;
      const gap = Math.abs(faces[i].area - (room.area ?? 0) * 10000);
      if (gap < bestGap) {
        best = i;
        bestGap = gap;
      }
    }
    if (best < 0) continue;
    const rival = claimedBy.get(best);
    if (rival) {
      const rivalGap = Math.abs(faces[best].area - (rival.area ?? 0) * 10000);
      // Strictly better keeps the face. An exact tie goes to the later
      // entry, which is the documented contract and is load-bearing: the
      // caller passes last pass's on-screen rooms first and the floor's saved
      // rooms last, and the two have identical areas. With `<=` the stale
      // unnamed copy won every tie, so a room the assistant had just renamed
      // kept reading "Room 1" on the plan.
      if (rivalGap < bestGap) continue;
      matched.delete(rival.id);
    }
    claimedBy.set(best, room);
    matched.add(room.id);
  }

  // Pass 2: exact wall sets, for rooms saved before anchors existed. This is
  // the old behaviour, kept only as a migration path.
  for (const room of persisted) {
    if (matched.has(room.id)) continue;
    for (let i = 0; i < faces.length; i++) {
      if (claimedBy.has(i)) continue;
      if (sameWallSet(room.walls, faces[i].room.walls)) {
        claimedBy.set(i, room);
        matched.add(room.id);
        break;
      }
    }
  }

  const rooms: Room[] = [];
  const fresh: Room[] = [];
  for (let i = 0; i < faces.length; i++) {
    const face = faces[i];
    const owner = claimedBy.get(i);
    // `walls` and `area` always come from the face -- they describe the
    // geometry as it is now, not as it was when the room was named.
    const next: Room = { ...face.room };
    if (owner) {
      next.id = owner.id;
      // Written out rather than looped over a key list: a `Record<string,
      // unknown>` cast to make a loop compile would also silence a genuine
      // mistake here. Keep in step with `CARRIED` in src/fpeval/roomid.py.
      if (owner.name !== undefined) next.name = owner.name;
      if (owner.floorTexture !== undefined) next.floorTexture = owner.floorTexture;
      if (owner.color !== undefined) next.color = owner.color;
      if (owner.roomType !== undefined) next.roomType = owner.roomType;
      if (owner.labelOffset !== undefined) next.labelOffset = owner.labelOffset;
      if (owner.anchor !== undefined) next.anchor = owner.anchor;
      // Re-anchor to the current centroid when the old anchor has drifted to
      // the edge, so a room stays findable after being resized repeatedly.
      if (!next.anchor || !pointInPolygon(next.anchor, face.polygon)) {
        next.anchor = face.centroid;
      }
    } else {
      next.id = mintRoomId(face.centroid);
      next.anchor = face.centroid;
      fresh.push(next);
    }
    rooms.push(next);
  }

  return {
    rooms,
    unmatched: persisted.filter((r) => !matched.has(r.id)),
    fresh,
  };
}
