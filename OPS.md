# Agent operations

What the agent can change, and by which path. Generated from `llm.OP_TABLE`
and `apply.py` -- if this table and the code disagree, the code is right and
this file is stale.

## Spec level -- edits the BRIEF, then re-solves (11)

| op | required | optional | editor mirror | checks attached |
|---|---|---|---|---|
| `add_room` | room_id, category | name, min_sqft, max_sqft, max_aspect, priority, optional, attached_bath, preferred_zone, storey | — | — |
| `remove_adjacency` | a, b | relation | — | — |
| `remove_room` | room_id | — | — | — |
| `set_adjacency` | a, b, kind | relation, reason | — | — |
| `set_entrance` | — | side, zone, via_foyer, avoid_direct_kitchen_view | — | — |
| `set_room_area` | room_id, min_sqft, max_sqft | — | — | — |
| `set_room_aspect` | room_id, max_aspect | min_aspect | — | — |
| `set_room_priority` | room_id, priority | — | — | — |
| `set_room_zone` | room_id, preferred_zone | — | — | — |
| `set_storeys` | value | — | — | — |
| `set_wet_grouping` | value | — | — | — |

## Geometry level -- edits the IR in place (10)

| op | required | optional | editor mirror | checks attached |
|---|---|---|---|---|
| `add_door` | wall_id, position | door_type, type, width_mm | `addDoor` | GEO, NBC, TOPO, SYNTAX, DESIGN |
| `add_wall` | start_ref, end_ref | thickness_mm | `addWall` | GEO, NBC, BYLAW, DESIGN, TOPO, ZONE, SYNTAX |
| `add_window` | wall_id, position | type, width_mm, height_mm, sill_mm | `addWindow` | GEO, NBC, DESIGN |
| `move_wall_parallel` | wall_id, direction, distance_mm | — | `moveWallParallel` | GEO, NBC, BYLAW, DESIGN, TOPO, ZONE, SYNTAX |
| `remove_element` | element_id | — | `removeElement` | GEO, NBC, BYLAW, DESIGN, TOPO, ZONE, SYNTAX |
| `split_wall` | wall_id, at | — | `splitWall` | GEO, TOPO, SYNTAX |
| `update_door` | door_id | width_mm, door_type, swing_direction, flip_side, position | `updateDoor` | GEO, NBC, TOPO, SYNTAX, DESIGN |
| `update_room` | room_id | name, category, room_type | `updateRoom` | TYPO, TOPO, ZONE, VASTU, BRIEF, NBC |
| `update_wall` | wall_id | thickness_mm, height_mm | `updateWall` | GEO, NBC, BYLAW |
| `update_window` | window_id | width_mm, height_mm, sill_mm, position | `updateWindow` | GEO, NBC, DESIGN |

## Furniture level -- edits placements in place (6)

| op | required | optional | editor mirror | checks attached |
|---|---|---|---|---|
| `furnish_room` | room_id | density, add, drop, swap | — | DESIGN, BRIEF |
| `move_item` | item_id, anchor | prefer, of, side, align, clear_front_mm | `moveFurniture` | DESIGN, BRIEF |
| `place_item` | room_id, item, anchor | prefer, avoid, of, side, count, align, clear_front_mm, gap_mm, abut, avoid_window, note | `addFurniture` | DESIGN, BRIEF |
| `remove_item` | item_id | — | `removeFurniture` | DESIGN, BRIEF |
| `replace_item` | item_id, item | — | `updateFurniture` | DESIGN, BRIEF |
| `set_kitchen_layout` | room_id | run, hob_zone, sink_zone, fridge_zone, breakfast_counter | — | DESIGN, VASTU, BRIEF |

