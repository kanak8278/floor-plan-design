export interface Point { x: number; y: number; }
export interface Wall { id: string; start: Point; end: Point; thickness: number; height: number; color: string; curvePoint?: Point; }
export type RoomCategory = 'indoor' | 'outdoor' | 'garage' | 'utility';
export interface Room { id: string; name: string; walls: string[]; floorTexture: string; area: number; color?: string; roomType?: RoomCategory; labelOffset?: Point; }
