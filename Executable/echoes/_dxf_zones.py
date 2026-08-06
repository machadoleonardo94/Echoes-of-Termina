"""
Topology-based zone detection: group connected SPLINE segments into closed loops.
Prints each loop's ID, segment count, and bounding box to confirm 12 zones.
"""
import math
from collections import defaultdict

DXF_PATH = 'tools/echoes/assets/LED_areas.dxf'
TOLERANCE = 0.5   # units — endpoints within this distance are treated as the same node

# ── Parse pairs ───────────────────────────────────────────────────────────────

def read_pairs(path):
    with open(path, 'r', errors='replace') as fh:
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
    return pairs

pairs = read_pairs(DXF_PATH)

# ── Extract SPLINE entities with all control points ───────────────────────────

splines = []
cur = None
for code, val in pairs:
    if code == 0:
        if cur is not None and cur['xs']:
            splines.append(cur)
        cur = None
        if val == 'SPLINE':
            cur = {'xs': [], 'ys': [], 'flags': 0}
        continue
    if cur is None:
        continue
    if code == 70: cur['flags'] = int(val)
    elif code == 10: cur['xs'].append(float(val))
    elif code == 20: cur['ys'].append(float(val))

if cur and cur['xs']:
    splines.append(cur)

print(f"Total SPLINE entities: {len(splines)}")

# ── Build segment list (start_pt, end_pt) ────────────────────────────────────

segs = []   # [(sx, sy, ex, ey), ...]
for sp in splines:
    if len(sp['xs']) >= 2:
        segs.append((sp['xs'][0], sp['ys'][0], sp['xs'][-1], sp['ys'][-1]))

# ── Union-Find to connect segments sharing endpoints ─────────────────────────

parent = list(range(len(segs)))

def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x

def union(a, b):
    parent[find(a)] = find(b)

def close(p1, p2):
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1]) < TOLERANCE

for i in range(len(segs)):
    for j in range(i + 1, len(segs)):
        si, ej = (segs[i][0], segs[i][1]), (segs[j][2], segs[j][3])
        ei, sj = (segs[i][2], segs[i][3]), (segs[j][0], segs[j][1])
        if (close(si, sj) or close(si, ej) or
                close(ei, sj) or close(ei, ej)):
            union(i, j)

# ── Group by component ────────────────────────────────────────────────────────

groups = defaultdict(list)
for i in range(len(segs)):
    groups[find(i)].append(i)

# Sort groups by Y position (top → bottom on mask)
def group_cy(indices):
    ys = [segs[k][1] for k in indices] + [segs[k][3] for k in indices]
    return sum(ys) / len(ys)

sorted_groups = sorted(groups.values(), key=lambda g: (-group_cy(g), len(g)))

print(f"Connected loop groups found: {len(sorted_groups)}\n")
print(f"{'ID':>3}  {'segs':>5}  {'cx':>8}  {'cy':>8}  "
      f"{'x_min':>8}  {'x_max':>8}  {'y_min':>8}  {'y_max':>8}")
print("─" * 68)

zone_data = []
for gid, indices in enumerate(sorted_groups, start=1):
    xs_all = [segs[k][0] for k in indices] + [segs[k][2] for k in indices]
    ys_all = [segs[k][1] for k in indices] + [segs[k][3] for k in indices]
    cx = sum(xs_all) / len(xs_all)
    cy = sum(ys_all) / len(ys_all)
    print(f"{gid:>3}  {len(indices):>5}  {cx:>8.2f}  {cy:>8.2f}  "
          f"{min(xs_all):>8.2f}  {max(xs_all):>8.2f}  "
          f"{min(ys_all):>8.2f}  {max(ys_all):>8.2f}")
    zone_data.append({
        'id': gid, 'cx': cx, 'cy': cy,
        'xmin': min(xs_all), 'xmax': max(xs_all),
        'ymin': min(ys_all), 'ymax': max(ys_all),
        'seg_indices': indices,
    })
