import re, glob, os, json, collections
from shapely.geometry import Polygon, Point, box
from shapely.ops import unary_union

HAB={'living','dining','bedroom','master_bedroom','kids_bedroom','guest_bedroom','study','family','pooja'}
WET={'bathroom','toilet','wc','powder'}
CIRC={'passage','foyer','lobby','corridor'}
SLEEP_LIVE={'living','dining','bedroom','master_bedroom','kids_bedroom','guest_bedroom','family'}

def load(path):
    s=open(path).read()
    hdr=dict(re.findall(r'data-(plan|scale|ox|oy)="([^"]+)"', s[:1200]))
    sc=float(hdr['scale']); ox=float(hdr['ox']); oy=float(hdr['oy'])
    rooms=[]
    for m in re.finditer(r'<polygon class="room room-([a-z_0-9]+)" points="([^"]+)"', s):
        rooms.append({'t':m.group(1),'p':Polygon([tuple(map(float,q.split(','))) for q in m.group(2).split()])})
    ops=[]
    for m in re.finditer(r'class="opening opening-([a-z_]+)"[^>]*data-wx="([-\d.]+)" data-wy="([-\d.]+)" data-t="[-\d.]+" data-width="([-\d.]+)"', s):
        ops.append({'k':m.group(1),'pt':(ox+float(m.group(2))/sc, oy+float(m.group(3))/sc),'w':float(m.group(4))})
    def poly(cls):
        m=re.search(cls+r'" points="([^"]+)"', s)
        return Polygon([tuple(map(float,q.split(','))) for q in m.group(1).split()]) if m else None
    plot=poly(r'class="plot-boundary'); setb=poly(r'class="setback-line')
    drv=poly(r'furn-driveway'); gate=poly(r'furn-fence_gate')
    for o in ops:
        pt=Point(o['pt']); o['r']=[i for i,r in enumerate(rooms) if r['p'].distance(pt)<0.75]
    labels=collections.defaultdict(dict)
    for m in re.finditer(r'<text class="room-label" data-owner="([^"]+)"[^>]*data-line="(\d)"[^>]*>([^<]*)</text>', s):
        labels[m.group(1)][int(m.group(2))]=m.group(3)
    return dict(plan=hdr['plan'],file=os.path.basename(path),sc=sc,rooms=rooms,ops=ops,plot=plot,setb=setb,drv=drv,gate=gate,labels=labels)

def audit(d):
    R=d['rooms']; O=d['ops']; sc=d['sc']; F=[]
    n=len(R)
    adj=collections.defaultdict(set)
    for o in O:
        if o['k'] in ('door','front_door'):
            for a in o['r']:
                for b in o['r']:
                    if a!=b: adj[a].add(b)
    ext=[i for o in O if o['k']=='front_door' for i in o['r']]
    entry=ext[0] if ext else None
    # BFS
    seen=set([entry]) if entry is not None else set()
    q=[entry] if entry is not None else []
    while q:
        c=q.pop()
        for m in adj[c]:
            if m not in seen: seen.add(m); q.append(m)
    def nm(i): return f"{R[i]['t']}#{i}"
    bal=[i for i,r in enumerate(R) if r['t']=='balcony']
    primary_balcony=max(bal, key=lambda i: R[i]['p'].area) if bal else None
    public_wc=[]
    for i,r in enumerate(R):
        t=r['t']; b=r['p'].bounds
        w=(b[2]-b[0])*sc; h=(b[3]-b[1])*sc; a=r['p'].area*sc**2/1e6
        wins=sum(1 for o in O if o['k']=='window' and i in o['r'])
        doors=[o for o in O if o['k'] in('door','front_door') and i in o['r']]
        if i not in seen: F.append(('UNREACHABLE', f"{nm(i)} has no door path from the front door"))
        if not doors: F.append(('NO_DOOR', f"{nm(i)} has no door at all"))
        if wins==0 and t in HAB:
            sev='NO_WINDOW_HAB' if t in SLEEP_LIVE else 'NO_WINDOW_HAB_SOFT'
            F.append((sev, f"{nm(i)} ({a:.1f} m2) has no window"))
        if wins==0 and t=='kitchen':
            # #25: an exhaust substitutes, and so does a utility with its own exterior opening
            relief=any(R[j]['t'] in ('utility','wash','balcony')
                       and any(o['k']=='window' and j in o['r'] for o in O) for j in adj[i])
            if not relief: F.append(('NO_WINDOW_KITCHEN', f"kitchen#{i} ({a:.1f} m2) has no window and no ventilated utility"))
        if wins==0 and t in WET: F.append(('NO_WINDOW_WET', f"{nm(i)} has no window"))
        # #67 DESIGN.BALCONY_ENCLOSED: a balcony is defined by an open edge, never by a window.
        ar=max(w,h)/max(1.0,min(w,h))
        if ar>=2.0 and t in HAB: F.append(('ROOM_ASPECT_SOFT', f"{nm(i)} {w/1000:.2f}x{h/1000:.2f}m aspect {ar:.2f}"))
        if t in ('bedroom','master_bedroom') and a<9.0: F.append(('BEDROOM_TOO_SMALL', f"{nm(i)} only {a:.1f} m2 ({w/1000:.2f}x{h/1000:.2f}m)"))
        # only route is through a private/wet room
        nb=set()
        for o in doors:
            nb |= {R[j]['t'] for j in o['r'] if j!=i}
        if len(doors)==1 and nb:
            only=list(nb)[0]
            if t in ('bedroom','master_bedroom','kitchen','living','dining','study') and only in ('bedroom','master_bedroom'):
                F.append(('THROUGH_BEDROOM', f"{nm(i)} is reachable only through a {only}"))
            if only in WET:
                sev='BATH_BEHIND_BATH' if t in WET else 'THROUGH_WET'
                F.append((sev, f"{nm(i)} is reachable only through a {only}"))
            if t=='balcony' and only in ('kitchen','utility') and i==primary_balcony:
                F.append(('PRIMARY_BALCONY_VIA_SERVICE',
                          f"the plan's largest balcony (#{i}) is reachable only through the {only}"))
            if t=='balcony' and only in WET: F.append(('BALCONY_VIA_WET', f"balcony#{i} is reachable only through a {only}"))
        if t in CIRC and wins==0 and a>=10: F.append(('BLIND_CIRCULATION', f"{nm(i)} is {a:.1f} m2 of circulation with no window"))
        if t in CIRC and len(doors)<=1: F.append(('DEADEND_CIRCULATION', f"{nm(i)} ({a:.1f} m2) is circulation with only {len(doors)} door"))
    # doors between two bedrooms
    for o in O:
        if o['k']!='door' or len(o['r'])!=2: continue
        a,b=[R[i]['t'] for i in o['r']]
        pair={a,b}
        if a in ('bedroom','master_bedroom') and b in ('bedroom','master_bedroom'):
            F.append(('BEDROOM_TO_BEDROOM_DOOR', f"door directly between {R[o['r'][0]]['t']}#{o['r'][0]} and {R[o['r'][1]]['t']}#{o['r'][1]}"))
        if pair<=WET: F.append(('WET_TO_WET_DOOR', f"door between two wet rooms ({a}#{o['r'][0]} - {b}#{o['r'][1]})"))
        if (a in WET and b in ('living','dining')) or (b in WET and a in ('living','dining')):
            public_wc.append(f"{a}#{o['r'][0]} into {b}#{o['r'][1]}")
        if (a in WET and b=='kitchen') or (b in WET and a=='kitchen'):
            F.append(('WC_OPENS_INTO_KITCHEN', f"{a} door opens directly into kitchen"))
        if (a=='kitchen' and b in ('bedroom','master_bedroom')) or (b=='kitchen' and a in ('bedroom','master_bedroom')):
            F.append(('KITCHEN_TO_BEDROOM_DOOR', f"door directly between kitchen and {('bedroom')}"))
    if len(public_wc)>=2:
        # #168: one public WC is the common toilet and belongs there; the second is the defect
        F.append(('MULTIPLE_WC_ON_PUBLIC', f"{len(public_wc)} toilet doors open into the living/dining: {'; '.join(public_wc)}"))
    # counts
    beds=[i for i,r in enumerate(R) if r['t'] in ('bedroom','master_bedroom')]
    baths=[i for i,r in enumerate(R) if r['t'] in WET]
    if beds and len(baths)==0: F.append(('NO_BATHROOM', 'no bathroom at all'))
    elif len(beds)>=3 and len(baths)==1: F.append(('ONE_BATH_MANY_BEDS', f"{len(beds)} bedrooms share 1 bathroom"))
    if not any(r['t']=='dining' for r in R) and any(r['t']=='living' for r in R):
        # #170: fine when the living can seat a table; 18 m2 is the smallest living that can
        biggest=max(r['p'].area*sc**2/1e6 for r in R if r['t']=='living')
        if biggest<18.0:
            F.append(('NO_DINING_LIVING_TOO_SMALL', f"no dining, and the largest living is only {biggest:.1f} m2"))
    # bath reachable without entering a bedroom?
    if baths:
        pub=set()
        if entry is not None:
            q=[entry]; pub={entry}
            while q:
                c=q.pop()
                if R[c]['t'] in ('bedroom','master_bedroom'): continue
                for m in adj[c]:
                    if m not in pub: pub.add(m); q.append(m)
        if not any(b in pub for b in baths):
            F.append(('NO_COMMON_TOILET', 'every toilet is behind a bedroom; no toilet reachable from the public zone'))
    # master bath
    mb=[i for i,r in enumerate(R) if r['t']=='master_bedroom']
    if mb and len(baths)>=2:
        # #165: a single-bathroom plan is right to share it; only 2+ baths owe the master one
        m=mb[0]
        if not any(b in adj[m] for b in baths):
            F.append(('MASTER_NO_ATTACHED_BATH', f'{len(baths)} bathrooms and none attached to the master'))
    # RULEBOOK additions
    env2=unary_union([r['p'] for r in R])
    for i,r in enumerate(R):
        if r['t'] in HAB or r['t']=='kitchen':
            # touches the outer boundary of the built envelope?
            if r['p'].exterior.intersection(env2.exterior).length*sc < 300:
                sev='NO_EXTERIOR_WALL' if (r['t'] in SLEEP_LIVE or r['t']=='kitchen') else 'NO_EXTERIOR_WALL_SOFT'
                F.append((sev, f"{nm(i)} has no exterior wall (fully landlocked)"))
    for i,r in enumerate(R):
        if r['t']=='kitchen':
            nb={R[j]['t'] for j in adj[i]}
            if not (nb & {'living','dining','passage','foyer','lobby'}):
                F.append(('KITCHEN_NO_PUBLIC_ACCESS', f"kitchen#{i} has no door to a living, dining or hall - only to {sorted(nb)}"))
            elif not (nb & {'living','dining'}):
                F.append(('KITCHEN_NOT_ON_DINING', f"kitchen#{i} has no direct door to a living or dining space (via {sorted(nb)})"))
    # circulation share
    tot=sum(r['p'].area for r in R)
    circ=sum(r['p'].area for r in R if r['t'] in CIRC)
    if tot and circ/tot>0.20:
        F.append(('CIRCULATION_HEAVY', f"circulation is {100*circ/tot:.0f}% of carpet area"))
    # A living room carrying the circulation is not a defect -- rejected on review; #80 wants it.
    # entry
    if entry is not None:
        F.append(('_ENTRY', f"front door opens into {nm(entry)}"))
        if R[entry]['t'] not in ('foyer','passage','lobby','living','dining'):
            F.append(('ENTRY_INTO_PRIVATE', f"front door opens directly into {nm(entry)}"))
        # #164: entering straight into a living or hall is normal. Only a door landing in
        # circulation owes the plan a named arrival space.
        if R[entry]['t'] in ('passage','corridor','lobby'):
            F.append(('ENTRY_INTO_CIRCULATION', f"front door opens into {nm(entry)}, a corridor, with no foyer"))
    # foyer not at entry
    for i,r in enumerate(R):
        if r['t']=='foyer':
            has_fd=any(o['k']=='front_door' and i in o['r'] for o in O)
            if not has_fd: F.append(('FOYER_NOT_AT_ENTRY', f"foyer#{i} is an interior room, not at the front door"))
    # site
    fd=[o for o in O if o['k']=='front_door']
    env=unary_union([r['p'] for r in R])
    eb=env.bounds
    if fd:
        p=fd[0]['pt']
        side='TOP' if abs(p[1]-eb[1])<3 else 'BOT' if abs(p[1]-eb[3])<3 else 'LEFT' if abs(p[0]-eb[0])<3 else 'RIGHT' if abs(p[0]-eb[2])<3 else 'INTERIOR'
        F.append(('_FD_SIDE', side))
    if d['gate'] is not None and d['plot'] is not None:
        g=d['gate'].centroid; pb=d['plot'].bounds
        gs='TOP' if abs(g.y-pb[1])<3 else 'BOT' if abs(g.y-pb[3])<3 else 'LEFT' if abs(g.x-pb[0])<3 else 'RIGHT' if abs(g.x-pb[2])<3 else 'INTERIOR'
        F.append(('_GATE_SIDE', gs))
        if fd and gs!=side:
            F.append(('GATE_OPPOSITE_FRONT_DOOR', f"gate is on the {gs} boundary but the front door is on the {side} wall"))
    if d['drv'] is not None:
        ov=d['drv'].intersection(env).area*sc**2/1e6
        if ov>0.5: F.append(('DRIVEWAY_OVER_BUILDING', f"driveway overlaps the building footprint by {ov:.1f} m2"))
        if d['plot'] is not None:
            out=d['drv'].difference(d['plot']).area*sc**2/1e6
            if out>0.5:
                # #175: no part of the drive may sit on land the plot does not own,
                # the road-facing apron included. Name the boundary -- a ~1 m2 front
                # overshoot and an 8-10 m2 side overshoot are different bugs.
                spill=d['drv'].difference(d['plot']); pb=d['plot'].bounds; ob=spill.bounds
                sides=[]
                if ob[1] < pb[1]-0.5: sides.append('front')
                if ob[3] > pb[3]+0.5: sides.append('rear')
                if ob[0] < pb[0]-0.5 or ob[2] > pb[2]+0.5: sides.append('side')
                F.append(('DRIVEWAY_OFF_PLOT',
                          f"driveway extends {out:.1f} m2 past the {'+'.join(sides) or 'plot'} boundary"))
        if d['gate'] is not None and d['drv'].distance(d['gate'])*sc>200:
            F.append(('DRIVEWAY_NOT_AT_GATE', f"driveway stops {d['drv'].distance(d['gate'])*sc:.0f} mm short of the gate"))
    if d['setb'] is not None:
        sb=d['setb'].bounds
        dd=[eb[0]-sb[0], eb[1]-sb[1], sb[2]-eb[2], sb[3]-eb[3]]
        if min(dd)<-0.5: F.append(('SETBACK_ENCROACH', f"building crosses the setback line by {(-min(dd))*sc:.0f} mm"))
    if d['plot'] is not None:
        oo=env.difference(d['plot']).area*sc**2/1e6
        if oo>0.5: F.append(('BUILDING_OFF_PLOT', f"building extends {oo:.1f} m2 outside the plot"))
    # label vs type
    for own,ls in d['labels'].items():
        pass
    return F

res={}
for f in sorted(glob.glob('out/suite_svg/*.svg')):
    d=load(f); res[d['file']]=audit(d)
json.dump({k:[list(x) for x in v] for k,v in res.items()}, open('/tmp/fpview/audit.json','w'), indent=1)
cnt=collections.Counter(c for v in res.values() for c,_ in v if not c.startswith('_'))
for c,k in cnt.most_common(): print(f"{k:4d}  {c}")
print("\nplans:", len(res))
