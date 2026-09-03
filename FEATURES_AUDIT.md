# OpenPlan3D feature audit

Audited from **code**, not from the repo's own docs. `FEATURES.md` and
`OUTDOOR_FEATURES.md` are aspirational planning documents that understate what
exists; `BUG_REPORT.md` overstates some problems (its "room detection is
fundamentally limited" finding is obsolete). Trust this file and the code.

Upstream `abb5267`, MIT, vendored at `vendor/openPlan3D/`. 24k LOC, 55 files.

## Data model (`src/lib/models/types.ts`)

| Type | Fields | Notes |
|---|---|---|
| `Wall` | id, start, end, thickness, height, color, `curvePoint?`, per-face colour/texture | **centreline + thickness**; `curvePoint` = quadratic bezier |
| `Door` | id, wallId, position 0..1, width, height, type, swingDirection, flipSide | parametric on host wall |
| `Window` | id, wallId, position, width, height, **sillHeight**, type | parametric |
| `Room` | id, name, walls[], floorTexture, area, color?, roomType, labelOffset? | **derived** from wall graph |
| `Stair` | id, position, rotation, width, depth, riserCount, direction, stairType | object, not a room |
| `Column` | id, position, rotation, shape, diameter, height, color | round or square |
| `FurnitureItem` | id, catalogId, position, rotation, scale, colour/dim/material overrides, locked | cm |
| `EntourageItem` | id, defId, position, width, rotation, opacity, locked | 2D presentation symbols |
| `Measurement`/`Annotation`/`TextAnnotation`/`GuideLine` | — | dimension + notes layer |
| `BackgroundImage` | dataUrl, position, scale, opacity, rotation, locked | trace over a scan |
| `Floor` | id, name, **level**, + all of the above per storey | multi-storey container |
| `Project` | id, name, floors[], activeFloorId, customEntourage | localStorage key `floorplan_projects` |

Units are **floating-point centimetres**. Our IR is integer millimetres and stays
authoritative; `Project` is a lossy projection.

## Editing surface (60 visible controls, verified in a browser)

- **Draw**: wall (click, dbl-click to finish), stairs, round/square column
- **Openings**: doors — single 90, double 150, sliding 180, french 150, pocket 90,
  bifold 180, doorway 100, garage 240 (cm); windows — standard 120x120, fixed
  100x100, casement 80x130, sliding 180x120, bay 200x150
- **Annotate**: text label, dimension, measure
- **Import**: background image, **Apple RoomPlan iOS LiDAR** (.json/.zip)
- **Edit**: select/pan, snap-to-grid, guides, layer visibility, rulers, minimap,
  alignment toolbar, context menu, command palette, group/ungroup
- **History**: undo/redo with `beginUndoGroup(...)`/`endUndoGroup(description)`,
  an undo-history panel, **and** a separate version history
- **Views**: Plan, **Elevation** (selected wall face-on), 2D/3D, print layout
- **Multi-floor**: add/remove/rename/duplicate floors, active-floor selector
- **Panels**: Area Summary, Properties, Layers, Settings, Build/Rooms/Objects
- **Export**: SVG, DXF, PDF (with title block), PNG, JSON. **DWG is a stub** —
  `cadExport.ts:204` alerts and exports DXF.
- **Shortcuts**: Ctrl+Z/Y/S, Escape, V select, H pan, W wall, T text, N dimension,
  M measure, F fit, G grid, S snap, L layers, ? help

## 3D (`ThreeViewer.svelte`, 2659 lines)

- Wall extrusion with interior/exterior materials, glass in windows, real door
  openings, door-type-specific geometry (sectional garage door panels)
- **`buildAllFloorsStacked`** — multi-storey stacked view
- **`setWallsXray` / `toggleWallTransparency`** — the closest thing to a cutaway.
  There are **no clipping planes**: a true section cut is not implemented.
- `viewTopDown`, `autoCenterCamera`, `autoCenterCameraAllFloors`
- **Walkthrough mode** (PointerLock first-person), `enterWalkthroughMode`
- **Sun / time of day**: `updateSunPosition`, `applyTimePreset`, `updateSkyGradient`,
  `updateAmbientIntensity` — directly useful for orientation and daylight
- Camera markers, `captureInteriorPhoto`, `renderCameraPreview`, `takeScreenshot`
- Procedural textures (`textureGenerator.ts`): brick, stone, wood panel, concrete,
  subway tile, hardwood. **Browser-only** (returns `HTMLCanvasElement`).
- AI: image-to-image restyling of the 3D render via Gemini/OpenAI. Cosmetic only —
  its own prompt says *"Do NOT change the room layout"*. Keys live in
  `localStorage` (`aiKeys.ts`), which is wrong for a product.

## Catalogues

**Furniture: 189 items**, `{id, name, category, icon, color, width, depth, height}` in cm,
some `symbol: true` (2D only). Distribution is heavily outdoor:

| Category | n | | Category | n |
|---|---|---|---|---|
| Landscaping | 57 | | Electrical | 8 |
| Outdoor Furniture | 13 | | Structures | 7 |
| Living Room | 11 | | Lighting | 7 |
| Decor | 11 | | Kitchen | 6 |
| Paths & Lawns | 10 | | Fencing | 6 |
| Garden Structures | 10 | | Plumbing | 5 |
| Pool & Spa | 8 | | Bedroom | 5 |
| Outdoor Lighting | 8 | | Bathroom | 5 |
| **Garage** | 8 | | Office / Dining | 2 / 2 |

Directly relevant to Indian homes:
- **Garage**: `car_sedan`, `car_suv`, `garage_door_single`, `garage_door_double`,
  `workbench`, `tool_cabinet`, **`bike`**, **`motorcycle`**
- **Fencing**: `fence_simple`, `fence_planks`, **`fence_gate`**, `fence_corner`,
  `picket_fence`, `metal_fence`
- **Paths & Lawns**: `driveway`, `path_straight`, `path_wide`, `stepping_stones`,
  `patio_stone`, `gravel_area`, `lawn_rect/square/large`, `sandbox`
- **Garden Structures**: `greenhouse`, `trellis`, `arbor`, `compost_bin`,
  `rain_barrel`, `bird_bath`, `fountain`, `statue`, `mailbox`
- **Structures**: `pergola`, `gazebo`, `shed`, `deck_patio`, `raised_garden_bed`
- **Plumbing symbols** (2D, height 0): `sym_water_supply`, `sym_drain`,
  `sym_water_heater`, `sym_washer_hookup`, `sym_gas_line`
- **Electrical symbols** (2D): outlet, switch, ceiling light, recessed, pendant,
  **ceiling fan**, junction box, smoke detector

**Entourage: 12** — `person`, `people-pair`, `car-sedan`, `car-suv`, `car-pickup`,
6 planting, `patio-umbrella`. Stored as SVG paths in a normalised viewBox, so they
render identically to canvas and SVG.

**Materials**: 15 floor (incl. `ceramic-white`, `porcelain`, `marble-white/dark`,
`slate`, `vinyl`), 22 wall colours (incl. `red-brick`, `subway-tile`, `stone`).

**Presets**: room shapes `rectangle`, `l-shape`, `t-shape`, `u-shape`;
house templates Studio, 1-Bedroom, 2-Bedroom, Open Concept, L-Shaped;
6 furnishing templates (Living/Bedroom/Kitchen/Bathroom/Office/Dining) — **but
these hardcode offsets assuming a 400x300 room and break on any other size.**

## Settings

`units` metric/imperial, `showDimensions`, `showExternalDimensions`,
`showInternalDimensions`, `showExtensionLines`, `showObjectDistance`,
`dimensionLineColor`, `wallMeasureMode` (centreline vs **edge/clear span**),
`snapToGrid`, `gridSize` (default 25 cm).

## Gaps and known defects

1. **No true section/cutaway** — x-ray walls only, no clipping planes.
2. **DWG export is a stub**; BBMP PreDCR needs real DWG via the ODA converter.
3. **No site/plot concept** — no plot polygon, setbacks, north bearing, FAR.
4. **Imperial units are systematically broken** (their own bug report): room area
   ignores imperial, properties-panel inputs don't convert, status bar always m²,
   ruler labels ignore units. This matters most for India — feet and sq ft.
5. **Three dimension toggles are dead**: external, internal, object-distance.
6. **Room identity is fragile** — reconciled by exact wall-set equality, so it
   survives moving a wall but not splitting/adding/deleting one.
7. **Two sources of truth for rooms** — persisted `floor.rooms` vs ephemeral
   `detectedRoomsStore`; 3D prefers the former, 2D the latter.
8. Room drag can move shared walls unexpectedly.
9. No headless/server API — pure client SPA (`adapter-node` available).
10. Missing from the catalogue for India: Indian squat WC, pooja mandir unit,
    sump / overhead tank / septic tank, chajja/sunshade, balcony railing.
    (`motorcycle`, `bike` and `fence_gate` DO exist — an earlier note of mine
    claiming otherwise was wrong.)
