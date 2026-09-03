import { readFileSync } from 'node:fs';
import { detectRooms, getRoomPolygon } from './roomDetection.ts';
const projects = JSON.parse(readFileSync('./samples.json','utf8'));
let exact=0, within1=0, crashed=0, areaErrs:number[]=[], detCounts:number[]=[], n=0;
for (const p of projects) {
  const fl = p.floors[0];
  let rooms;
  try { rooms = detectRooms(fl.walls); } catch(e){ crashed++; continue; }
  n++;
  const exp = p._expect.n_rooms;
  detCounts.push(rooms.length);
  if (rooms.length === exp) exact++;
  if (Math.abs(rooms.length - exp) <= 1) within1++;
  // compare sorted area multisets where counts match
  if (rooms.length === exp) {
    const got = rooms.map((r:any)=>r.area).sort((a:number,b:number)=>a-b);
    const want = p._expect.areas_m2;
    for (let i=0;i<got.length;i++){
      if (want[i]>0.5) areaErrs.push(Math.abs(got[i]-want[i])/want[i]);
    }
  }
  // every door/window must resolve to an existing wall id
  const ids = new Set(fl.walls.map((w:any)=>w.id));
  for (const d of [...fl.doors, ...fl.windows]) if(!ids.has(d.wallId)) throw new Error('orphan opening '+d.id);
}
const q=(a:number[],p:number)=>{if(!a.length)return NaN;const s=[...a].sort((x,y)=>x-y);return s[Math.floor(p/100*(s.length-1))];};
console.log(`=== OpenPlan3D detectRooms() on converted Projects (n=${n}, crashed=${crashed}) ===`);
console.log(`  room count exact      : ${(100*exact/n).toFixed(1)}%`);
console.log(`  room count within +/-1: ${(100*within1/n).toFixed(1)}%`);
console.log(`  detected rooms/plan   : median=${q(detCounts,50)} p90=${q(detCounts,90)}`);
console.log(`  per-room area error   : median=${(100*q(areaErrs,50)).toFixed(3)}%  p90=${(100*q(areaErrs,90)).toFixed(3)}%  n=${areaErrs.length}`);
console.log(`  orphan openings       : none (would have thrown)`);
