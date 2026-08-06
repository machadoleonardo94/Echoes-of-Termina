"""
DXF zone inspector — areas_clean.dxf
Run from workspace root:  python tools/echoes/_dxf_inspect.py
"""
import math, sys
from collections import defaultdict, Counter

DXF_PATH = 'tools/echoes/assets/areas_clean.dxf'
TOL = 0.8   # endpoint match tolerance (DXF units)

# ── pair reader ───────────────────────────────────────────────────────────────
with open(DXF_PATH, 'r', errors='replace') as fh:
    raw = fh.read().replace('\r\n', '\n').replace('\r', '\n')
lines = raw.split('\n')
pairs = []
i = 0
while i + 1 < len(lines):
    try:
        pairs.append((int(lines[i].strip()), lines[i + 1].strip()))
    except ValueError:
        pass
    i += 2

# ── entity splitter ───────────────────────────────────────────────────────────
GEO = {'LWPOLYLINE','SPLINE','TEXT','MTEXT','CIRCLE','ARC','HATCH','INSERT','LINE'}
ents = []
cur  = None
for code, val in pairs:
    if code == 0:
        if cur: ents.append(cur)
        cur = {'type': val, 'handle': '', 'layer': '0',
               'flags': 0, 'xs': [], 'ys': [], 'text': ''}
    if not cur: continue
    if code == 5:  cur['handle'] = val
    if code == 8:  cur['layer']  = val
    if code == 70: cur['flags']  = int(val)
    if code == 10: cur['xs'].append(float(val))
    if code == 20: cur['ys'].append(float(val))
    if code == 1:  cur['text']   = val
if cur: ents.append(cur)

geo = [e for e in ents if e['type'] in GEO]

# ── report totals ─────────────────────────────────────────────────────────────
print('Entity counts:', dict(Counter(e['type'] for e in geo)))
print()

# ── LWPOLYLINE ────────────────────────────────────────────────────────────────
polys = [e for e in geo if e['type'] == 'LWPOLYLINE']
print(f'LWPOLYLINE ({len(polys)}):')
for p in polys:
    xs, ys = p['xs'], p['ys']
    if not xs: continue
    cx, cy = sum(xs)/len(xs), sum(ys)/len(ys)
    closed = bool(p['flags'] & 1)
    print(f'  h={p["handle"]:<5} flags={p["flags"]} closed={closed} '
          f'pts={len(xs)} cx={cx:.2f} cy={cy:.2f}  '
          f'x=[{min(xs):.1f}..{max(xs):.1f}] y=[{min(ys):.1f}..{max(ys):.1f}]')
print()

# ── SPLINE topology ───────────────────────────────────────────────────────────
splines = [e for e in geo if e['type'] == 'SPLINE']
segs = [(sp['xs'][0], sp['ys'][0], sp['xs'][-1], sp['ys'][-1], sp)
        for sp in splines if len(sp['xs']) >= 2]

par = list(range(len(segs)))
def find(x):
    while par[x] != x: par[x] = par[par[x]]; x = par[x]
    return x
def union(a, b): par[find(a)] = find(b)
def close(ax,ay,bx,by): return math.hypot(ax-bx,ay-by) < TOL

for i,(x0,y0,x1,y1,_) in enumerate(segs):
    for j,(x2,y2,x3,y3,_) in enumerate(segs):
        if i >= j: continue
        if (close(x1,y1,x2,y2) or close(x1,y1,x3,y3) or
                close(x0,y0,x2,y2) or close(x0,y0,x3,y3)):
            union(i, j)

groups = defaultdict(list)
for i in range(len(segs)): groups[find(i)].append(i)

def bounds(idxs):
    xs = [c for k in idxs for c in segs[k][4]['xs']]
    ys = [c for k in idxs for c in segs[k][4]['ys']]
    return min(xs), max(xs), min(ys), max(ys)
def ctr(idxs):
    x0,x1,y0,y1 = bounds(idxs); return (x0+x1)/2, (y0+y1)/2

# Sort: top-to-bottom, then left-to-right (matches image reading order)
sg = sorted(groups.values(), key=lambda g: (-ctr(g)[1], ctr(g)[0]))
print(f'SPLINE connected loops ({len(sg)}):')
for i, g in enumerate(sg, 1):
    x0,x1,y0,y1 = bounds(g); cx,cy = ctr(g)
    print(f'  loop {i:>2}: segs={len(g):<4} cx={cx:8.2f} cy={cy:8.2f}  '
          f'x=[{x0:.1f}..{x1:.1f}] y=[{y0:.1f}..{y1:.1f}]')
print()

# ── TEXT / MTEXT ──────────────────────────────────────────────────────────────
texts = [e for e in geo if e['type'] in ('TEXT', 'MTEXT')]
print(f'TEXT/MTEXT ({len(texts)}):')
for t in texts:
    x = t['xs'][0] if t['xs'] else 0; y = t['ys'][0] if t['ys'] else 0
    print(f'  h={t["handle"]:<5} text="{t["text"]}" @ ({x:.2f},{y:.2f})')
print()
print(f'TOTAL zones (SPLINE loops + LWPOLYLINE): {len(sg) + len(polys)}')

sys.exit(0)

# ── 1. Build flat (code, value) pair list ─────────────────────────────────────

def read_pairs(path):
    with open(path, 'r', errors='replace') as fh:
        raw = fh.read().replace('\r\n', '\n').replace('\r', '\n')
    lines = raw.split('\n')
    pairs = []
    i = 0
    while i + 1 < len(lines):
        code_str = lines[i].strip()
        val_str  = lines[i + 1].strip()
        try:
            pairs.append((int(code_str), val_str))
        except ValueError:
            pass
        i += 2
    return pairs

pairs = read_pairs(DXF_PATH)

# ── 2. Split into entity dicts ────────────────────────────────────────────────

ENTITY_TYPES = {'LWPOLYLINE', 'POLYLINE', 'SPLINE', 'HATCH', 'INSERT',
                'TEXT', 'MTEXT', 'ARC', 'CIRCLE', 'LINE', 'ELLIPSE'}

# Also capture entity handles and text strings
_HANDLE_KEY = 'handle'
_TEXT_KEY   = 'text'

entities = []
cur = None

for code, val in pairs:
    if code == 0:
        if cur is not None:
            entities.append(cur)
        if val in ENTITY_TYPES:
            cur = {'type': val, 'xs': [], 'ys': []}
        else:
            cur = None
        continue
    if cur is None:
        continue
    if   code == 5:   cur['handle'] = val
    elif code == 8:   cur['layer']  = val
    elif code == 1:   cur['text']   = val
    elif code == 70:  cur['flags']  = int(val)
    elif code == 90:  cur['vcount'] = int(val)
    elif code == 10:  cur['xs'].append(float(val))
    elif code == 20:  cur['ys'].append(float(val))
    elif code == 11:  cur.setdefault('x2s', []).append(float(val))
    elif code == 21:  cur.setdefault('y2s', []).append(float(val))

if cur is not None:
    entities.append(cur)

# ── 3. Separate by type ───────────────────────────────────────────────────────

texts    = [e for e in entities if e['type'] in ('TEXT', 'MTEXT')]
splines  = [e for e in entities if e['type'] == 'SPLINE']
polys    = [e for e in entities if e['type'] == 'LWPOLYLINE']
hatches  = [e for e in entities if e['type'] == 'HATCH']
circles  = [e for e in entities if e['type'] == 'CIRCLE']

# ── 4. Helpers ────────────────────────────────────────────────────────────────

def bbox(xs, ys):
    if not xs or not ys:
        return None
    return min(xs), max(xs), min(ys), max(ys)

def centroid(xs, ys):
    return sum(xs) / len(xs), sum(ys) / len(ys)

def dist(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)

# ── 5. Report ─────────────────────────────────────────────────────────────────

print(f'{"="*60}')
from collections import Counter
all_types = Counter(e['type'] for e in entities)
print(f'Total geometry entities: {len(entities)}')
for t, n in sorted(all_types.items()): print(f'  {t:<16} {n}')
print()

print()
print('TEXT / MTEXT labels:')
if texts:
    for t in texts:
        x = t['xs'][0] if t['xs'] else 0
        y = t['ys'][0] if t['ys'] else 0
        print(f'  h={t.get("handle","?"):<6} "{t.get("text","?")}"  @ ({x:.2f}, {y:.2f})')
else:
    print('  (none)')

print()
print(f'{"─"*60}')
print('SPLINE / LWPOLYLINE zones (with nearest text label):')

shapes = [(e, e['xs'], e['ys']) for e in splines + polys if e['xs'] and e['ys']]

for e, xs, ys in shapes:
    cx, cy = centroid(xs, ys)
    bb = bbox(xs, ys)
    closed = bool(e.get('flags', 0) & 1)
    npts = len(xs)
    # Find nearest text label
    nearest = min(texts, key=lambda t: dist(
        cx, cy,
        t["xs"][0] if t["xs"] else 0,
        t["ys"][0] if t["ys"] else 0
    )) if texts else None
    label = nearest.get('text', '?') if nearest else '?'
    print(f'  {e["type"]:<12} pts={npts:<4} closed={str(closed):<6} '
          f'center=({cx:7.2f},{cy:7.2f})  '
          f'bb=[{bb[0]:.1f}..{bb[1]:.1f} x {bb[2]:.1f}..{bb[3]:.1f}]  '
          f'nearest_label={label}')

print()
print(f'Total SPLINE loops: {len(sorted_grps)},  LWPOLYLINE zones: {len(polys)},  combined: {len(sorted_grps)+len(polys)}')
