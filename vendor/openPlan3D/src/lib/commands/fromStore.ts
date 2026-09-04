/**
 * Editor updates -> command params.
 *
 * The store's mutators take `Partial<Wall>`, `Partial<Door>` and friends in
 * float centimetres, because that is what `Project` is. Commands are integer
 * millimetres with the IR's field names. This module is the one place that
 * translation happens, so a rename on either side has exactly one site to fix
 * rather than ten scattered through `stores/project.ts`.
 *
 * Every mapper drops keys the caller did not supply. `undefined` means "not
 * changed" throughout the command vocabulary, so a partial update stays
 * partial — thickening a wall must not also reassert its colour.
 */
import type {
  Wall, Door, Window as Win, FurnitureItem, Stair, Column, Room,
  EntourageItem, TextAnnotation, BackgroundImage, Point,
} from '$lib/models/types';
import { mm, pt } from '$lib/commands/bus';

type Params = Record<string, unknown>;

/** Copy `key -> target` when the source actually carries it. */
function put(out: Params, value: unknown, key: string,
             transform: (v: never) => unknown = (v) => v): void {
  if (value !== undefined) out[key] = transform(value as never);
}

export function wallUpdateParams(id: string, u: Partial<Wall>): Params {
  const out: Params = { wall_id: id };
  put(out, u.thickness, 'thickness_mm', (v: number) => mm(v));
  put(out, u.height, 'height_mm', (v: number) => mm(v));
  put(out, u.color, 'color');
  put(out, u.texture, 'texture');
  put(out, u.interiorColor, 'interior_color');
  put(out, u.interiorTexture, 'interior_texture');
  put(out, u.exteriorColor, 'exterior_color');
  put(out, u.exteriorTexture, 'exterior_texture');
  return out;
}

export function doorUpdateParams(id: string, u: Partial<Door>): Params {
  const out: Params = { opening_id: id };
  put(out, u.width, 'width_mm', (v: number) => mm(v));
  put(out, u.type, 'door_type');
  put(out, u.swingDirection, 'swing_direction');
  put(out, u.flipSide, 'flip_side');
  put(out, u.position, 'at');
  // `height` on a door is the head height above floor level in the IR.
  put(out, u.height, 'head_mm', (v: number) => mm(v));
  return out;
}

export function windowUpdateParams(id: string, u: Partial<Win>): Params {
  const out: Params = { opening_id: id };
  put(out, u.width, 'width_mm', (v: number) => mm(v));
  put(out, u.type, 'window_type');
  put(out, u.position, 'at');
  put(out, u.sillHeight, 'sill_mm', (v: number) => mm(v));
  // A window's `height` is its own extent, so the head is sill + height. Only
  // derivable when both are known; the caller sends whichever it changed and
  // the service recomputes from the opening it already holds.
  if (u.height !== undefined && u.sillHeight !== undefined) {
    out.head_mm = mm(u.sillHeight + u.height);
  }
  return out;
}

export function furnitureUpdateParams(id: string,
                                      u: Partial<FurnitureItem>): Params {
  const out: Params = { furniture_id: id };
  put(out, u.rotation, 'rotation');
  put(out, u.width, 'width_mm', (v: number) => mm(v));
  put(out, u.depth, 'depth_mm', (v: number) => mm(v));
  put(out, u.height, 'height_mm', (v: number) => mm(v));
  put(out, u.color, 'color');
  put(out, u.material, 'material');
  put(out, u.locked, 'locked');
  if (u.scale !== undefined) {
    out.scale_x = u.scale.x;
    out.scale_y = u.scale.y;
    out.scale_z = u.scale.z;
  }
  return out;
}

export function stairUpdateParams(id: string, u: Partial<Stair>): Params {
  const out: Params = { stair_id: id };
  put(out, u.rotation, 'rotation');
  put(out, u.width, 'width_mm', (v: number) => mm(v));
  put(out, u.depth, 'depth_mm', (v: number) => mm(v));
  put(out, u.riserCount, 'riser_count');
  put(out, u.direction, 'direction');
  put(out, u.stairType, 'stair_type');
  return out;
}

export function columnUpdateParams(id: string, u: Partial<Column>): Params {
  const out: Params = { column_id: id };
  put(out, u.shape, 'shape');
  put(out, u.diameter, 'size_mm', (v: number) => mm(v));
  put(out, u.height, 'height_mm', (v: number) => mm(v));
  put(out, u.rotation, 'rotation');
  put(out, u.color, 'color');
  return out;
}

/**
 * A room update splits in two: `labelOffset` is a coordinate and so belongs to
 * the `direct` family, everything else is symbolic. Returning both keeps the
 * caller from having to know that.
 */
export function roomUpdateParams(
  id: string,
  u: Partial<{ name: string; floorTexture: string; color: string;
               roomType: Room['roomType']; labelOffset: Point | undefined }>,
): { room: Params | null; label: Params | null } {
  const room: Params = { room_id: id };
  put(room, u.name, 'name');
  put(room, u.floorTexture, 'floor_texture');
  put(room, u.color, 'color');
  put(room, u.roomType, 'room_class');
  const label = u.labelOffset
    ? { room_id: id, offset_x: mm(u.labelOffset.x), offset_y: mm(u.labelOffset.y) }
    : null;
  return {
    room: Object.keys(room).length > 1 ? room : null,
    label,
  };
}

export function entourageUpdateParams(id: string,
                                      u: Partial<EntourageItem>): Params {
  const out: Params = { entourage_id: id };
  put(out, u.width, 'width_mm', (v: number) => mm(v));
  put(out, u.rotation, 'rotation');
  put(out, u.opacity, 'opacity');
  put(out, u.locked, 'locked');
  return out;
}

export function textUpdateParams(
  id: string,
  u: Partial<{ x: number; y: number; text: string; fontSize: number;
               color: string; rotation: number }>,
): { text: Params | null; move: Params | null } {
  const text: Params = { text_id: id };
  put(text, u.text, 'text');
  put(text, u.fontSize, 'font_size');
  put(text, u.color, 'color');
  put(text, u.rotation, 'rotation');
  const move = u.x !== undefined && u.y !== undefined
    ? { text_id: id, position: { x: mm(u.x), y: mm(u.y) } }
    : null;
  return { text: Object.keys(text).length > 1 ? text : null, move };
}

export function dimensionUpdateParams(
  id: string,
  u: Partial<{ x1: number; y1: number; x2: number; y2: number;
               offset: number; label: string }>,
): Params | null {
  const out: Params = { dimension_id: id };
  put(out, u.label, 'label');
  return Object.keys(out).length > 1 ? out : null;
}

export function backgroundParams(bg: BackgroundImage | undefined): Params {
  // No params clears the image, which is what `setBackgroundImage(undefined)`
  // means in the store.
  if (!bg) return {};
  return {
    data_url: bg.dataUrl,
    position: pt(bg.position),
    scale: bg.scale,
    opacity: bg.opacity,
    rotation: bg.rotation,
    locked: bg.locked,
  };
}

export function backgroundUpdateParams(u: Partial<BackgroundImage>): Params {
  const out: Params = {};
  put(out, u.dataUrl, 'data_url');
  if (u.position !== undefined) out.position = pt(u.position);
  put(out, u.scale, 'scale');
  put(out, u.opacity, 'opacity');
  put(out, u.rotation, 'rotation');
  put(out, u.locked, 'locked');
  return out;
}

/** A key that collapses successive frames of one gesture into one command. */
export function gestureKey(kind: string, id: string, fields: Params): string {
  return `${kind}:${id}:${Object.keys(fields).sort().join(',')}`;
}
