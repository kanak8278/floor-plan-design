/**
 * GENERATED FILE -- do not edit.
 *
 *   python -m fpeval.gen_ts
 *
 * The command vocabulary is defined in `src/fpeval/commands.py`, next to the
 * applier that implements it. This is the browser's view of the same table.
 * `tests/test_commands_ts.py` fails if the two drift.
 */

export const VOCABULARY_VERSION = 1;

/** Every command name the service implements. */
export type CommandOp =
  | 'add_column'
  | 'add_dimension'
  | 'add_door'
  | 'add_entourage'
  | 'add_furniture'
  | 'add_guide'
  | 'add_measurement'
  | 'add_room'
  | 'add_stair'
  | 'add_storey'
  | 'add_text'
  | 'add_wall'
  | 'add_wall_between'
  | 'add_window'
  | 'duplicate_furniture'
  | 'duplicate_opening'
  | 'duplicate_wall'
  | 'group_elements'
  | 'move_column'
  | 'move_entourage'
  | 'move_furniture'
  | 'move_guide'
  | 'move_room_label'
  | 'move_stair'
  | 'move_text'
  | 'move_wall_by'
  | 'move_wall_endpoint'
  | 'move_wall_parallel'
  | 'place_furniture_in_room'
  | 'remove_adjacency'
  | 'remove_element'
  | 'remove_room'
  | 'remove_storey'
  | 'rename_design'
  | 'replace_storey'
  | 'set_active_storey'
  | 'set_adjacency'
  | 'set_background'
  | 'set_entrance'
  | 'set_room_area'
  | 'set_room_aspect'
  | 'set_room_priority'
  | 'set_room_zone'
  | 'set_site'
  | 'set_storeys'
  | 'set_wet_grouping'
  | 'split_wall'
  | 'ungroup_elements'
  | 'update_column'
  | 'update_dimension'
  | 'update_entourage'
  | 'update_furniture'
  | 'update_opening'
  | 'update_room'
  | 'update_stair'
  | 'update_text'
  | 'update_wall';

export const COMMAND_OPS: CommandOp[] = ['add_column', 'add_dimension', 'add_door', 'add_entourage', 'add_furniture', 'add_guide', 'add_measurement', 'add_room', 'add_stair', 'add_storey', 'add_text', 'add_wall', 'add_wall_between', 'add_window', 'duplicate_furniture', 'duplicate_opening', 'duplicate_wall', 'group_elements', 'move_column', 'move_entourage', 'move_furniture', 'move_guide', 'move_room_label', 'move_stair', 'move_text', 'move_wall_by', 'move_wall_endpoint', 'move_wall_parallel', 'place_furniture_in_room', 'remove_adjacency', 'remove_element', 'remove_room', 'remove_storey', 'rename_design', 'replace_storey', 'set_active_storey', 'set_adjacency', 'set_background', 'set_entrance', 'set_room_area', 'set_room_aspect', 'set_room_priority', 'set_room_zone', 'set_site', 'set_storeys', 'set_wet_grouping', 'split_wall', 'ungroup_elements', 'update_column', 'update_dimension', 'update_entourage', 'update_furniture', 'update_opening', 'update_room', 'update_stair', 'update_text', 'update_wall'];

export type CommandFamily = 'symbolic' | 'direct';
export type CommandSource = 'user' | 'agent' | 'solver' | 'import';

export interface CommandSpec {
  family: CommandFamily;
  required: string[];
  optional: string[];
  /** The store function the optimistic client uses, if it has one. */
  editor: string | null;
  doc: string;
}

export const COMMANDS: Record<CommandOp, CommandSpec> = {
  'add_column': {
    family: 'direct',
    required: ["column_id", "position"],
    optional: ["shape", "size_mm", "height_mm", "rotation"],
    editor: 'addColumn',
    doc: 'Place a structural column.',
  },
  'add_dimension': {
    family: 'direct',
    required: ["dimension_id", "start", "end"],
    optional: ["offset", "label"],
    editor: 'addAnnotation',
    doc: 'Add a dimension string to the sheet.',
  },
  'add_door': {
    family: 'symbolic',
    required: ["opening_id", "wall_id", "at"],
    optional: ["door_type", "width_mm", "kind"],
    editor: 'addDoor',
    doc: 'Add a door to a wall. `at` is a fraction 0..1 or a position word.',
  },
  'add_entourage': {
    family: 'direct',
    required: ["entourage_id", "def_id", "position", "width_mm"],
    optional: ["rotation", "opacity"],
    editor: 'addEntourageItem',
    doc: 'Place a presentation symbol (car, tree, figure).',
  },
  'add_furniture': {
    family: 'direct',
    required: ["furniture_id", "catalog_id", "position"],
    optional: ["rotation", "width_mm", "depth_mm", "height_mm", "room_id"],
    editor: 'addFurniture',
    doc: 'Place a catalogue item at a point.',
  },
  'add_guide': {
    family: 'direct',
    required: ["guide_id", "orientation", "position"],
    optional: [],
    editor: 'addGuide',
    doc: 'Drop a guide line.',
  },
  'add_measurement': {
    family: 'direct',
    required: ["measurement_id", "start", "end"],
    optional: [],
    editor: 'addMeasurement',
    doc: 'Drop an ad-hoc ruler.',
  },
  'add_room': {
    family: 'symbolic',
    required: ["room_id", "category"],
    optional: ["name", "min_sqft", "max_sqft", "max_aspect", "priority", "optional", "attached_bath", "preferred_zone", "storey"],
    editor: null,
    doc: 'Add a programme entry, then re-solve.',
  },
  'add_stair': {
    family: 'direct',
    required: ["stair_id", "position"],
    optional: ["rotation", "width_mm", "depth_mm", "riser_count", "direction", "stair_type", "room_id"],
    editor: 'addStair',
    doc: 'Place a staircase. A stair is an object inside a room, not a room.',
  },
  'add_storey': {
    family: 'symbolic',
    required: ["storey_id"],
    optional: ["name", "level", "copy_from", "id_map"],
    editor: 'addFloor',
    doc: 'Add a floor, optionally copying an existing one. When copying, `id_map` gives the old-id -> new-id mapping the author used, so both sides end up calling the copied walls the same thing.',
  },
  'add_text': {
    family: 'direct',
    required: ["text_id", "position", "text"],
    optional: ["font_size", "color", "rotation"],
    editor: 'addTextAnnotation',
    doc: 'Add a note to the sheet.',
  },
  'add_wall': {
    family: 'direct',
    required: ["wall_id", "start", "end"],
    optional: ["thickness_mm", "height_mm"],
    editor: 'addWall',
    doc: 'Draw a wall between two points. The id is supplied so replay is deterministic.',
  },
  'add_wall_between': {
    family: 'symbolic',
    required: ["wall_id", "start_ref", "end_ref"],
    optional: ["thickness_mm", "height_mm"],
    editor: 'addWall',
    doc: 'Draw a wall between two symbolic references (\'w7:start\', \'w7@0.5\'). The applier resolves them, so no coordinate is authored.',
  },
  'add_window': {
    family: 'symbolic',
    required: ["opening_id", "wall_id", "at"],
    optional: ["window_type", "width_mm", "sill_mm", "head_mm"],
    editor: 'addWindow',
    doc: 'Add a window to a wall.',
  },
  'duplicate_furniture': {
    family: 'symbolic',
    required: ["furniture_id", "new_furniture_id"],
    optional: [],
    editor: 'duplicateFurniture',
    doc: 'Copy an item, offset by the editor\'s default nudge.',
  },
  'duplicate_opening': {
    family: 'symbolic',
    required: ["opening_id", "new_opening_id"],
    optional: [],
    editor: 'duplicateDoor',
    doc: 'Copy an opening along its host wall.',
  },
  'duplicate_wall': {
    family: 'symbolic',
    required: ["wall_id", "new_wall_id"],
    optional: [],
    editor: 'duplicateWall',
    doc: 'Copy a wall, offset by the editor\'s default nudge.',
  },
  'group_elements': {
    family: 'symbolic',
    required: ["group_id", "element_ids"],
    optional: [],
    editor: 'createGroup',
    doc: 'Group elements so they move together.',
  },
  'move_column': {
    family: 'direct',
    required: ["column_id", "position"],
    optional: [],
    editor: 'moveColumn',
    doc: 'Drag a column.',
  },
  'move_entourage': {
    family: 'direct',
    required: ["entourage_id", "position"],
    optional: [],
    editor: 'moveEntourage',
    doc: 'Drag a presentation symbol.',
  },
  'move_furniture': {
    family: 'direct',
    required: ["furniture_id", "position"],
    optional: [],
    editor: 'moveFurniture',
    doc: 'Drag an item. Emitted on release.',
  },
  'move_guide': {
    family: 'direct',
    required: ["guide_id", "position"],
    optional: [],
    editor: 'moveGuide',
    doc: 'Slide a guide line.',
  },
  'move_room_label': {
    family: 'direct',
    required: ["room_id", "offset_x", "offset_y"],
    optional: [],
    editor: 'updateRoom',
    doc: 'Nudge a room\'s label off its centroid.',
  },
  'move_stair': {
    family: 'direct',
    required: ["stair_id", "position"],
    optional: [],
    editor: 'moveStair',
    doc: 'Drag a staircase.',
  },
  'move_text': {
    family: 'direct',
    required: ["text_id", "position"],
    optional: [],
    editor: 'moveTextAnnotation',
    doc: 'Drag a note.',
  },
  'move_wall_by': {
    family: 'direct',
    required: ["wall_id", "dx", "dy"],
    optional: [],
    editor: 'moveWallParallel',
    doc: 'Slide a whole wall by a vector.',
  },
  'move_wall_endpoint': {
    family: 'direct',
    required: ["wall_id", "endpoint", "position"],
    optional: [],
    editor: 'moveWallEndpoint',
    doc: 'Drag one end of a wall. Emitted once on release, not per frame.',
  },
  'move_wall_parallel': {
    family: 'symbolic',
    required: ["wall_id", "direction", "distance_mm"],
    optional: [],
    editor: 'moveWallParallel',
    doc: 'Slide a wall a stated distance in a compass direction. The applier resolves the bearing against Site.north_deg.',
  },
  'place_furniture_in_room': {
    family: 'symbolic',
    required: ["furniture_id", "catalog_id", "room_id"],
    optional: ["against", "facing", "beside"],
    editor: null,
    doc: 'Ask for an item in a room with a relational anchor (\'against the north wall\'). The placement solver resolves coordinates.',
  },
  'remove_adjacency': {
    family: 'symbolic',
    required: ["a", "b"],
    optional: ["relation"],
    editor: null,
    doc: 'Drop an adjacency constraint.',
  },
  'remove_element': {
    family: 'symbolic',
    required: ["element_id"],
    optional: [],
    editor: 'removeElement',
    doc: 'Delete anything by id. Removing a wall cascades its openings.',
  },
  'remove_room': {
    family: 'symbolic',
    required: ["room_id"],
    optional: [],
    editor: null,
    doc: 'Drop a programme entry, then re-solve.',
  },
  'remove_storey': {
    family: 'symbolic',
    required: ["storey_id"],
    optional: [],
    editor: 'removeFloor',
    doc: 'Delete a floor.',
  },
  'rename_design': {
    family: 'symbolic',
    required: ["name"],
    optional: [],
    editor: 'updateProjectName',
    doc: 'Rename the design.',
  },
  'replace_storey': {
    family: 'symbolic',
    required: ["storey_id"],
    optional: ["reason"],
    editor: null,
    doc: 'Swap a whole storey for freshly solved geometry. The payload is the solver\'s output, carried out of band -- this command records that it happened so the log stays a complete history.',
  },
  'set_active_storey': {
    family: 'symbolic',
    required: ["storey_id"],
    optional: [],
    editor: 'setActiveFloor',
    doc: 'Switch which floor is being edited.',
  },
  'set_adjacency': {
    family: 'symbolic',
    required: ["a", "b", "kind"],
    optional: ["relation", "reason"],
    editor: null,
    doc: 'Add or overwrite an adjacency requirement.',
  },
  'set_background': {
    family: 'direct',
    required: [],
    optional: ["data_url", "position", "scale", "opacity", "rotation", "locked"],
    editor: 'setBackgroundImage',
    doc: 'Set or adjust the traced background image. No params clears it.',
  },
  'set_entrance': {
    family: 'symbolic',
    required: [],
    optional: ["side", "zone", "via_foyer", "avoid_direct_kitchen_view"],
    editor: null,
    doc: 'Change where the front door is, then re-solve.',
  },
  'set_room_area': {
    family: 'symbolic',
    required: ["room_id", "min_sqft", "max_sqft"],
    optional: [],
    editor: null,
    doc: 'Widen or shift a room\'s target area range, then re-solve.',
  },
  'set_room_aspect': {
    family: 'symbolic',
    required: ["room_id", "max_aspect"],
    optional: ["min_aspect"],
    editor: null,
    doc: 'Relax or tighten a room\'s long/short ratio limit.',
  },
  'set_room_priority': {
    family: 'symbolic',
    required: ["room_id", "priority"],
    optional: [],
    editor: null,
    doc: 'Change how hard the solver fights for this room.',
  },
  'set_room_zone': {
    family: 'symbolic',
    required: ["room_id", "preferred_zone"],
    optional: [],
    editor: null,
    doc: 'Move a room\'s preferred compass zone.',
  },
  'set_site': {
    family: 'symbolic',
    required: [],
    optional: ["north_deg", "setbacks_mm"],
    editor: null,
    doc: 'Set orientation or setbacks. The plot polygon comes from the envelope calculation, not from a command.',
  },
  'set_storeys': {
    family: 'symbolic',
    required: ["value"],
    optional: [],
    editor: null,
    doc: 'Change the storey count, then re-solve.',
  },
  'set_wet_grouping': {
    family: 'symbolic',
    required: ["value"],
    optional: [],
    editor: null,
    doc: 'Change wet-room grouping preference.',
  },
  'split_wall': {
    family: 'symbolic',
    required: ["wall_id", "at", "new_wall_id"],
    optional: [],
    editor: 'splitWall',
    doc: 'Split a wall at a parametric position. `at` is \'midpoint\' or 0..1.',
  },
  'ungroup_elements': {
    family: 'symbolic',
    required: ["group_id"],
    optional: [],
    editor: 'ungroup',
    doc: 'Dissolve a group.',
  },
  'update_column': {
    family: 'symbolic',
    required: ["column_id"],
    optional: ["shape", "size_mm", "height_mm", "rotation", "color"],
    editor: 'updateColumn',
    doc: 'Change a column\'s shape or size.',
  },
  'update_dimension': {
    family: 'symbolic',
    required: ["dimension_id"],
    optional: ["label"],
    editor: 'updateAnnotation',
    doc: 'Retext a dimension string.',
  },
  'update_entourage': {
    family: 'symbolic',
    required: ["entourage_id"],
    optional: ["width_mm", "rotation", "opacity", "locked"],
    editor: 'updateEntourageItem',
    doc: 'Resize or rotate a presentation symbol.',
  },
  'update_furniture': {
    family: 'symbolic',
    required: ["furniture_id"],
    optional: ["rotation", "width_mm", "depth_mm", "height_mm", "color", "material", "locked", "scale_x", "scale_y", "scale_z", "room_id"],
    editor: 'updateFurniture',
    doc: 'Rotate, resize, restyle, or lock an item.',
  },
  'update_opening': {
    family: 'symbolic',
    required: ["opening_id"],
    optional: ["width_mm", "door_type", "window_type", "swing_direction", "flip_side", "at", "sill_mm", "head_mm"],
    editor: 'updateDoor',
    doc: 'Change an opening\'s width, type, swing, or position on its wall.',
  },
  'update_room': {
    family: 'symbolic',
    required: ["room_id"],
    optional: ["name", "category", "room_class", "floor_texture", "color"],
    editor: 'updateRoom',
    doc: 'Relabel or retype a room. Never geometry -- a room is a face of the wall graph, so it changes when walls do. Use `category` for what the room IS (bedroom, kitchen, pooja: the 18-type taxonomy every rule reads). `room_class` is only OpenPlan3D\'s four-value floor-rendering bucket (indoor/outdoor/garage/utility) and is derived from `category` unless you override it, which you almost never should.',
  },
  'update_stair': {
    family: 'symbolic',
    required: ["stair_id"],
    optional: ["rotation", "width_mm", "depth_mm", "riser_count", "direction", "stair_type", "room_id"],
    editor: 'updateStair',
    doc: 'Change a staircase\'s geometry or type.',
  },
  'update_text': {
    family: 'symbolic',
    required: ["text_id"],
    optional: ["text", "font_size", "color", "rotation"],
    editor: 'updateTextAnnotation',
    doc: 'Edit a note.',
  },
  'update_wall': {
    family: 'symbolic',
    required: ["wall_id"],
    optional: ["thickness_mm", "height_mm", "color", "texture", "interior_color", "interior_texture", "exterior_color", "exterior_texture"],
    editor: 'updateWall',
    doc: 'Change a wall\'s thickness, height, or finishes.',
  },
};

/**
 * Param names that count as absolute geometry. A `symbolic` command may not
 * carry one: the model emits intent, solvers emit coordinates
 * (DECISIONS.md #6). The service enforces this too — this copy is so the
 * client can refuse before making a round trip.
 */
export const COORDINATE_KEYS: ReadonlySet<string> = new Set(["anchor", "center", "centre", "dx", "dy", "end", "offset_x", "offset_y", "point", "points", "polygon", "position", "start", "x", "x1", "x2", "y", "y1", "y2"]);

export const COLUMN_SHAPES = ["round", "square"] as const;
export const COMPASS = ["north", "north_east", "east", "south_east", "south", "south_west", "west", "north_west"] as const;
export const DOOR_TYPES = ["single", "double", "sliding", "french", "pocket", "bifold", "opening", "garage"] as const;
export const ENDPOINTS = ["start", "end"] as const;
export const ORIENTATIONS = ["horizontal", "vertical"] as const;
export const POSITION_WORDS = ["start", "quarter", "centre", "three_quarter", "end"] as const;
export const ROOM_CLASSES = ["indoor", "outdoor", "garage", "utility"] as const;
export const STAIR_TYPES = ["straight", "l-shaped", "u-shaped", "spiral"] as const;
export const WINDOW_TYPES = ["standard", "fixed", "casement", "sliding", "bay"] as const;

/** Is this command shaped correctly? Mirrors `Command.validate` minus the
 *  referential checks, which need the document and so happen server-side. */
export function validateShape(
  op: string,
  params: Record<string, unknown>,
  source: CommandSource = 'user',
): string[] {
  const spec = (COMMANDS as Record<string, CommandSpec>)[op];
  if (!spec) return [`unknown command '${op}'`];
  const errors: string[] = [];
  if (spec.family === 'symbolic') {
    for (const key of Object.keys(params)) {
      if (COORDINATE_KEYS.has(key)) {
        errors.push(`${op}: '${key}' is a coordinate, and ${op} is symbolic`);
      }
    }
  }
  if (spec.family === 'direct' && source === 'agent') {
    errors.push(`${op}: an agent may not author a direct command`);
  }
  const allowed = new Set([...spec.required, ...spec.optional]);
  for (const key of spec.required) {
    if (params[key] === undefined || params[key] === null) {
      errors.push(`${op}: missing required param '${key}'`);
    }
  }
  for (const key of Object.keys(params)) {
    if (!allowed.has(key)) errors.push(`${op}: unexpected param '${key}'`);
  }
  return errors;
}
