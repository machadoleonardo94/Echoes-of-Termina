"""Independent verification of the exported speaker-case meshes.

Parses the STL files, then re-derives volume / area / bounding box from the
triangle soup and checks that each mesh is a closed, manifold solid.  Nothing
here trusts the OpenSCAD echo output - it is the cross-check for it.
"""
import os
import struct
import sys
import numpy as np

# Vertex weld tolerance in mm for topology tests.  Kept tight (1 nm) so that
# genuinely distinct nearby vertices are never merged -- merging them would
# fabricate non-manifold edges.  OpenSCAD's ASCII STL writes identical CGAL
# vertices with up to ~5e-7 mm of rounding noise, so a shell can appear split;
# that is measured separately by `shell_gaps()` and is not a hole.
WELD_TOL = 1e-9


def read_stl(path):
    """Binary vs ASCII decided by the binary header's own triangle count."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(84)
    if len(head) == 84:
        (n,) = struct.unpack("<I", head[80:84])
        if size == 84 + n * 50:
            with open(path, "rb") as fh:
                return _read_binary(fh)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return _read_ascii(fh)


def _read_binary(fh):
    fh.seek(80)
    (n,) = struct.unpack("<I", fh.read(4))
    raw = fh.read(n * 50)
    a = np.frombuffer(raw, dtype=np.uint8).reshape(n, 50)
    tri = a[:, 12:48].copy().view("<f4").reshape(n, 3, 3).astype(np.float64)
    return tri


def _read_ascii(fh):
    verts = []
    for line in fh:
        s = line.strip()
        if s.startswith("vertex"):
            verts.append([float(x) for x in s.split()[1:4]])
    v = np.asarray(verts, dtype=np.float64)
    return v.reshape(-1, 3, 3)


def stats(tri):
    v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
    cross = np.cross(v1 - v0, v2 - v0)
    area = 0.5 * np.linalg.norm(cross, axis=1)
    signed = np.einsum("ij,ij->i", v0, np.cross(v1, v2)) / 6.0
    pts = tri.reshape(-1, 3)
    return {
        "tris": len(tri),
        "volume": abs(signed.sum()),
        "signed_volume": signed.sum(),
        "area": area.sum(),
        "lo": pts.min(axis=0),
        "hi": pts.max(axis=0),
        "size": pts.max(axis=0) - pts.min(axis=0),
    }


def _edges(tri, tol=WELD_TOL):
    """Undirected edges as canonical (2,3) vertex pairs.

    tri is (T,3,3); key[:, [0,1]] therefore selects two *vertices* of each
    triangle, and sorting along axis=1 makes the edge directionless.
    """
    key = np.round(tri / tol).astype(np.int64)          # (T,3,3)
    e = np.concatenate([key[:, [0, 1]], key[:, [1, 2]], key[:, [2, 0]]], axis=0)
    return np.sort(e, axis=1)                           # (3T,2,3)


def manifold(tri, tol=WELD_TOL):
    """Every edge of a closed solid must be shared by exactly two triangles."""
    e = _edges(tri, tol)
    uniq, counts = np.unique(e, axis=0, return_counts=True)
    return (len(uniq), int((counts == 2).sum()),
            int((counts > 2).sum()), int((counts == 1).sum()))


def euler(tri, tol=WELD_TOL):
    """V - E + F; must be 2 for a closed solid of genus 0."""
    key = np.round(tri / tol).astype(np.int64).reshape(-1, 3)
    v = len(np.unique(key, axis=0))
    e = len(np.unique(_edges(tri, tol), axis=0))
    return v - e + len(tri)


def shells(tri, tol=WELD_TOL):
    """Split the mesh into vertex-connected shells; returns list of vertex arrays."""
    key = np.round(tri / tol).astype(np.int64).reshape(-1, 3)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = np.asarray(inv).reshape(-1, 3)
    parent = list(range(int(inv.max()) + 1))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b, c in inv:
        for u, v in ((a, b), (b, c), (c, a)):
            ru, rv = find(int(u)), find(int(v))
            if ru != rv:
                parent[ru] = rv
    groups = {}
    for i in range(len(parent)):
        groups.setdefault(find(i), []).append(i)
    return [uniq[idx].astype(np.float64) * tol for idx in groups.values()]


def shell_gaps(tri, tol=WELD_TOL):
    """How detached is the worst shell, i.e. max over shells of (min distance
    from that shell to any other shell).

    A well-formed CGAL solid exported through ASCII STL can come back as a few
    shells whose vertices differ only by export rounding.  This metric is ~1e-7
    for that case and large only if a shell is genuinely floating free.
    """
    sh = shells(tri, tol)
    if len(sh) < 2:
        return 0.0, len(sh), []
    per_shell = []
    for i in range(len(sh)):
        best = np.inf
        a = sh[i]
        for j in range(len(sh)):
            if i == j:
                continue
            b = sh[j]
            d2 = ((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2)
            best = min(best, float(np.sqrt(d2.min())))
        per_shell.append(best)
    return max(per_shell), len(sh), per_shell


def report(name, path, expect=None):
    tri = read_stl(path)
    st = stats(tri)
    ne, ok, nonman, open_ = manifold(tri)
    chi = euler(tri)
    gap, nshell, per_shell = shell_gaps(tri)
    watertight = (nonman == 0 and open_ == 0)
    genus = (2 - chi) / 2 if (nshell == 1 and (2 - chi) % 2 == 0) else None
    print(f"--- {name}")
    print(f"    file          : {path.split(chr(92))[-1]}")
    print(f"    triangles     : {st['tris']}")
    print(f"    bbox min      : ({st['lo'][0]:8.3f}, {st['lo'][1]:8.3f}, {st['lo'][2]:8.3f})")
    print(f"    bbox max      : ({st['hi'][0]:8.3f}, {st['hi'][1]:8.3f}, {st['hi'][2]:8.3f})")
    print(f"    size LxWxH    : {st['size'][0]:.3f} x {st['size'][1]:.3f} x {st['size'][2]:.3f} mm")
    print(f"    volume        : {st['volume']:12.1f} mm3  = {st['volume']/1000:8.3f} cm3")
    print(f"    surface area  : {st['area']:12.1f} mm2")
    print(f"    edge sharing  : unique={ne}, exactly-2-faces={ok}, "
          f">2-faces={nonman}, 1-face={open_}")
    topo = f"Euler V-E+F = {chi}, shells = {nshell}"
    if genus is not None:
        topo += f", genus {genus:g} = {genus:g} through-hole(s)"
    print(f"    topology      : {topo}")
    if nshell > 1:
        det = ("coincident to within export rounding - ONE physical piece"
               if gap < 1e-3 else "genuinely separate parts")
        print(f"    shell detach  : worst shell is {gap:.3e} mm from the rest "
              f"-> {det}")
        print(f"                    per-shell: "
              + ", ".join(f"{d:.1e}" for d in per_shell) + " mm")
    print(f"    WATERTIGHT    : {'YES - no boundary and no non-manifold edges' if watertight else 'NO'}")
    if expect:
        print(f"    EXPECTED      : {expect}")
    return st


LABELS = {
    "part_A_front_bezel.stl": "PART A  front bezel (print orientation)",
    "part_B_back_cup.stl": "PART B  back cup (print orientation)",
    "print_plate.stl": "PRINT PLATE (both parts on one plate)",
    "assembly.stl": "ASSEMBLY (both parts, assembled pose)",
    "exploded.stl": "EXPLODED (joint opened)",
    "section_case.stl": "SECTION half case",
    "cavity.stl": "CAVITY  net trapped air volume",
    "driver_ghost.stl": "DRIVER keep-out (half)",
    "driver_full.stl": "DRIVER keep-out (full)",
}


if __name__ == "__main__":
    # Usage: python mesh_check.py <dir-of-stls> [substring-filter ...]
    base = sys.argv[1] if len(sys.argv) > 1 else "."
    only = [a.lower() for a in sys.argv[2:]]
    files = [f for f in sorted(os.listdir(base)) if f.lower().endswith(".stl")]
    if only:
        files = [f for f in files if any(o in f.lower() for o in only)]
    if not files:
        print(f"no .stl files found in {os.path.abspath(base)}")
        sys.exit(1)
    for f in files:
        report(LABELS.get(f, f), os.path.join(base, f))
