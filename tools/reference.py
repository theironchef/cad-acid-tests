"""Sketches of what each acid test should look like, built from TESTS.md with no CAD kernel.

Each test's final stage is written here as an implicit solid: a signed distance function in numpy, made of exact
primitives (boxes, cylinders, cones, spheres, tori) combined by booleans, shells as offset bands, and patterns as
folds. It is sphere-traced by tools/render.py into docs/img/<test>-sketch.png, in the same style as the harnesses'
renders of what each program built.

Each sketch is checked against expected/values.py: its volume is sampled on a grid (the inside fraction of each
cell from the distance) and compared with the analytic value at the latest stage that values.py pins down. The
results go to docs/img/sketches.json, which tools/site.py reads.

Approximations, which the page names per test: rounds are rolling-ball blends of the two surfaces' distances
(exact for a 90-degree edge between planes, close elsewhere) and leave out corner patches and rounds of the edges
a cut makes later; the modelled thread is drawn as its plain minor-diameter hole; the spline outline is sampled.

python tools/reference.py [acid-1 ...] [--no-volume]
needs numpy and Pillow
"""
import json
import math
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "expected"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import values  # noqa: E402
import render  # noqa: E402


# ------------------------------------------------------------------ primitives and operations
def box(x, y, z, lo, hi):
    return render._box_sdf(np, x, y, z, lo, hi)


def _cap(dr, da):
    return np.sqrt(np.maximum(dr, 0) ** 2 + np.maximum(da, 0) ** 2) + np.minimum(np.maximum(dr, da), 0)


def cyl(x, y, z, axis, c, r, a0, a1):
    """A cylinder of radius r along axis 'x', 'y' or 'z' through c (the other two coordinates), from a0 to a1."""
    s, u, v = {"x": (x, y, z), "y": (y, x, z), "z": (z, x, y)}[axis]
    return _cap(np.hypot(u - c[0], v - c[1]) - r, np.maximum(a0 - s, s - a1))


def sphere(x, y, z, c, r):
    return np.sqrt((x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2) - r


def torus(x, y, z, R, r):
    return np.hypot(np.hypot(x, y) - R, z) - r


def rect(x, y, cx, cy, hx, hy, r=0.0):
    """A rectangle (half sizes hx, hy) with corners rounded to r, in the plane."""
    qx, qy = np.abs(x - cx) - (hx - r), np.abs(y - cy) - (hy - r)
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r


def union(*ds):
    out = ds[0]
    for d in ds[1:]:
        out = np.minimum(out, d)
    return out


def inter(*ds):
    out = ds[0]
    for d in ds[1:]:
        out = np.maximum(out, d)
    return out


def sub(a, *bs):
    return inter(a, *[-b for b in bs])


def rmax(a, b, r):
    """The intersection of a and b with their edge rounded by a ball of radius r (a rolling-ball blend)."""
    return np.minimum(np.maximum(a, b), -r) + np.hypot(np.maximum(a + r, 0), np.maximum(b + r, 0))


def shell(outer, opened, t):
    """Walls of thickness t inside outer; opened is the same solid without the faces the shell removes."""
    return np.maximum(outer, -(opened + t))


def fold(x, y, step_deg, start_deg=0.0, count=None, lo=None, hi=None):
    """Turn each point into the frame of its nearest instance of a circular pattern about Z."""
    th = np.degrees(np.arctan2(y, x))
    k = np.round((th - start_deg) / step_deg)
    if count:
        k = np.mod(k, count)
    if lo is not None:
        k = np.clip(k, lo, hi)
    ph = np.radians(start_deg + k * step_deg)
    c, s = np.cos(ph), np.sin(ph)
    return x * c + y * s, -x * s + y * c


# ------------------------------------------------------------------ acid-2's outline: the spline's distance field
class Outline:
    """Signed distance to the C2 periodic spline through values.SPLINE_PTS, tabulated on a fine grid."""
    G = 0.2

    def __init__(self):
        pts = [values.SPLINE.at(i, t / 200) for i in range(len(values.SPLINE_PTS)) for t in range(200)]
        P = np.array(pts)
        A, B = P, np.roll(P, -1, axis=0)
        self.x0, self.y0 = -72.0, -48.0
        xs = np.arange(self.x0, 72.0 + 1e-9, self.G)
        ys = np.arange(self.y0, 52.0 + 1e-9, self.G)
        gx, gy = np.meshgrid(xs, ys, indexing="ij")
        q = np.stack([gx.ravel(), gy.ravel()], 1)
        out = np.empty(len(q))
        AB = B - A
        L2 = (AB ** 2).sum(1)
        for s in range(0, len(q), 3000):
            Q = q[s:s + 3000, None, :]
            t = np.clip(((Q - A) * AB).sum(2) / L2, 0, 1)
            dist = np.sqrt((((A + t[..., None] * AB) - Q) ** 2).sum(2)).min(1)
            qy = Q[..., 1]
            cross = ((A[:, 1] > qy) != (B[:, 1] > qy)) & (
                Q[..., 0] < A[:, 0] + (qy - A[:, 1]) * AB[:, 0] / np.where(AB[:, 1] == 0, 1, AB[:, 1]))
            out[s:s + 3000] = np.where(cross.sum(1) % 2 == 1, -dist, dist)
        self.grid = out.reshape(len(xs), len(ys))
        self.xmax, self.ymax = xs[-1], ys[-1]

    def __call__(self, x, y):
        gx = np.clip((x - self.x0) / self.G, 0, self.grid.shape[0] - 1.000001)
        gy = np.clip((y - self.y0) / self.G, 0, self.grid.shape[1] - 1.000001)
        i, j = np.floor(gx).astype(int), np.floor(gy).astype(int)
        fx, fy = gx - i, gy - j
        g = self.grid
        v = (g[i, j] * (1 - fx) * (1 - fy) + g[i + 1, j] * fx * (1 - fy) + g[i, j + 1] * (1 - fx) * fy
             + g[i + 1, j + 1] * fx * fy)
        ox = np.maximum(np.maximum(self.x0 - x, x - self.xmax), 0)
        oy = np.maximum(np.maximum(self.y0 - y, y - self.ymax), 0)
        return v + np.hypot(ox, oy)


_OUTLINE = []


def outline(x, y):
    if not _OUTLINE:
        _OUTLINE.append(Outline())
    return _OUTLINE[0](x, y)


# ------------------------------------------------------------------ the tests: f(x, y, z, stage) in the local frame
BX = math.sqrt(30 ** 2 - 0.95001 ** 2)


def acid1(x, y, z, n=8):
    tor = torus(x, y, z, 30, 8)
    half = rmax(tor, -z, 0.8) if n >= 4 else np.maximum(tor, -z)
    boss = cyl(x, y, z, "z", (BX, 0.95001), 0.95, -1.9, 0.5) if n >= 2 else np.inf
    notch = cyl(x, y, z, "z", (0, 38), 0.7, -1, 3) if n >= 3 else -np.inf
    d = sub(union(half, boss), notch)
    if n >= 5:  # inward, the boss's end face removed
        opened = sub(union(half, cyl(x, y, z, "z", (BX, 0.95001), 0.95, -50, 0.5)), notch)
        d = shell(d, opened, 0.6)
    if n >= 6:
        u, v = fold(x, y, 30, 15, 12)
        d = sub(d, cyl(u, v, z, "z", (30, 0), 0.5, -20, 20))
    return d  # stage 7 splits at Z = 4 and stage 8 moves it: the same material


def acid2_part(x, y, z, n=8):
    side = outline(x, y) - (z * math.tan(math.radians(3)) if n >= 4 else 0)
    top = rmax(side, z - 25, 3) if n >= 2 else np.maximum(side, z - 25)
    if n >= 3:
        bosses = lambda a0: union(*[cyl(x, y, z, "z", (cx, cy), 3, a0, 35) for cx in (-30, 30) for cy in (-12, 12)])
        d = np.maximum(union(top, bosses(20)), -z)
        if n >= 5:  # inward, the bottom face removed
            d = shell(d, union(top, bosses(-50)), 1.5)
        if n >= 6:
            d = sub(d, *[cyl(x, y, z, "z", (cx, cy), 2, -5, 45) for cx in (-30, 30) for cy in (-12, 12)])
        return d
    return np.maximum(top, -z)


def acid2(x, y, z, n=8):
    d = acid2_part(x, y, z, n)
    if n >= 7:
        d = union(d, acid2_part(160 - x, y, z, n))
    if n >= 8:
        d = union(d, box(x, y, z, (55, -5, 0), (105, 5, 5)))
    return d


def acid3(x, y, z, n=7):
    blk = np.maximum(rmax(rect(x, y, 40, 30, 40, 30), z - 30, 0.5), -z) if n >= 7 else box(x, y, z, (0, 0, 0), (80, 60, 30))
    sharp = box(x, y, z, (0, 0, 0), (80, 60, 30))
    pads = union(*[box(x, y, z, (18.5 + 20 * i, 18.5 + 20 * j, 29), (21.5 + 20 * i, 21.5 + 20 * j, 32))
                   for i in range(3) for j in range(2)]) if n >= 2 else np.inf
    cuts = []
    if n >= 3:
        cuts.append(cyl(x, y, z, "z", (40, 30), 4.917 / 2, 10, 33))
    if n >= 4:
        u, v = fold(x - 40, y - 30, 45, 0, 8)
        cuts.append(cyl(u, v, z, "z", (12, 0), 1.25, 20, 33))
    if n >= 5:
        cuts.append(cyl(x, y, z, "z", (1.50001, 0), 1.5, 26, 33))
    d = sub(union(blk, pads), *cuts) if cuts else union(blk, pads)
    if n >= 6:  # closed: no face removed
        d = shell(d, sub(union(sharp, pads), *cuts), 1.2)
    return d


def acid4(x, y, z, n=6):
    if n >= 6:
        q = [np.abs(c) - 18 for c in (x, y, z)]
        cube = np.sqrt(sum(np.maximum(c, 0) ** 2 for c in q)) + np.minimum(np.maximum(q[0], np.maximum(q[1], q[2])), 0) - 2
    else:
        cube = box(x, y, z, (-20, -20, -20), (20, 20, 20))
    cuts = []
    if n >= 2:
        cuts += [cyl(x, y, z, "x", (0, 0), 9, -21, 21), cyl(x, y, z, "y", (0, 0), 9, -21, 21),
                 cyl(x, y, z, "z", (0, 0), 9, -21, 21)]
    if n >= 3:
        cuts += [cyl(x, y, z, "x", (yy, 0), 4, -21, 21) for yy in (-10, 10)]
        cuts += [cyl(x, y, z, "y", (xx, 1e-5), 4, -21, 21) for xx in (-10, 10)]
    d = sub(cube, *cuts) if cuts else cube
    if n >= 4:
        other = union(cyl(x, y, z, "z", (26, 0), 5, -30, 30), cyl(x, y, z, "z", (35.99999, 0), 5, -30, 30))
        if n >= 5:
            other = union(other, sphere(x, y, z, (26, 0, 34.99999), 5))
        d = union(d, other)
    return d


def acid5(x, y, z, n=6):
    side = rect(x, y, 15, 10, 15, 10, 1.3 if n >= 2 else 0)
    top = rmax(side, z - 10, 1.29) if n >= 3 else np.maximum(side, z - 10)
    d = np.maximum(top, -z)  # stages 4 and 5's 0.00002 chamfer and 0.000025 round are far below a pixel
    if n >= 6:
        d = shell(d, top, 0.4)
    return d


Z6 = 499.99975
R_TIP = 0.5 - 1000 * math.tan(math.radians(0.02))


def acid6(x, y, z, n=6):
    planet = np.maximum(sphere(x, y, z, (0, 0, 0), 500), z - Z6)
    if n >= 2:
        planet = union(planet, cyl(x, y, z, "z", (0, 0), 0.06, Z6 - 0.01, Z6 + 0.12))
    d = planet
    if n >= 3:
        ring = np.maximum(torus(x, y, z - 600, 500, 0.5), 600 - z)
        if n >= 4:
            ring = shell(ring, ring, 0.06)
        d = union(d, ring)
    if n >= 5:
        s = z + 1600
        rz = 0.5 + (R_TIP - 0.5) * s / 1000
        needle = np.maximum((np.hypot(x, y) - rz) * math.cos(math.atan((0.5 - R_TIP) / 1000)), np.maximum(-s, s - 1000))
        d = union(d, needle)
    if n >= 6:
        d = union(d, cyl(x, y, z, "z", (0, 0), 0.4, -600, -400))
    return d


T18 = math.tan(math.radians(18))
C18 = math.cos(math.radians(18))


def acid7(x, y, z, n=6):
    rho = np.hypot(x, y)
    if n >= 3:
        side = (rho - (100 - z * T18)) * C18
    else:
        a = math.atan(60 / 200)
        side = (rho - (100 - z * 60 / 200)) * math.cos(a)
    top = rmax(side, z - 200, 0.5) if n >= 4 else np.maximum(side, z - 200)
    cuts = []
    if n >= 2:
        u, v = fold(x, y, 60, 0, 6)
        cuts.append(cyl(u, v, z, "z", (32, 0), 5, 150, 201))
    pocket = (lambda a0: cyl(x, y, z, "z", (0, 0), 60, a0, 80)) if n >= 5 else None
    d = np.maximum(top, -z)
    d = sub(d, *(cuts + ([pocket(-1)] if pocket else []))) if cuts or pocket else d
    if n >= 6:  # inward, the bottom face removed
        d = shell(d, sub(top, *(cuts + [pocket(-1000)])), 2)
    return d


def acid8_drum(x, y, z, n=7):
    d = cyl(x, y, z, "z", (0, 0), 50, 0, 120)
    if n >= 2:
        u, v = fold(x, y, 15, 0, 24)
        zk = 20 + 20 * np.clip(np.round((z - 20) / 20), 0, 4 if n >= 3 else 0)
        d = sub(d, cyl(u, v, z - zk, "x", (0, 0), 3, 30, 51))
    if n >= 4:
        u, v = fold(x, y, 15, 0, lo=0, hi=6)
        d = union(d, cyl(u, v, z, "z", (30, 0), 2, 119.5, 123))
    if n >= 5:  # closed: no face removed
        d = shell(d, d, 2)
    return d


def acid8(x, y, z, n=7):
    d = acid8_drum(x, y, z, n)  # stage 6 splits at Z = 61: the same material
    if n >= 7:  # the upper body (Z >= 61) mirrored across Z = 140
        d = union(d, np.maximum(acid8_drum(x, y, 280 - z, n), 61 - (280 - z)))
    return d


def acid9_side(x, y, z):
    t = z / 100  # each corner moves straight to the corner 45 degrees further around
    wr, wi = 1 - t + t * math.sqrt(0.5), t * math.sqrt(0.5)
    ph = np.arctan2(wi, wr)
    h = 20 * np.hypot(wr, wi)
    c, s = np.cos(ph), np.sin(ph)
    u, v = x * c + y * s, -x * s + y * c
    return rect(u, v, 0, 0, h, h) * 0.8  # the twist stretches distances: scaled to stay a lower bound


def acid9(x, y, z, n=6):
    side = acid9_side(x, y, z)
    bore = cyl(x, y, z, "z", (0, 0), 36.94 / 2, -1000, 1000) if n >= 2 else -np.inf
    if n >= 3:
        sph = sphere(x, y, z, (0, 0, 50), 55)
        wall = rmax(side, sph, 1) if n >= 4 else np.maximum(side, sph)
    else:
        wall = side
    d = sub(inter(wall, -z, z - 100), bore)
    if n >= 5:  # inward, the top face removed
        d = shell(d, sub(np.maximum(wall, -z), bore), 1)
    return d  # stage 6 splits it through the axis: the same material


def acid10(x, y, z, n=6):
    blk = np.maximum(rect(x, y, 25, 25, 25, 25, 5 if n >= 5 else 0), np.maximum(-z, z - 50))
    d = blk
    if n >= 2:
        d = union(d, cyl(x, y, z, "x", (25, 59.99998), 10, 0, 50))
    if n >= 3:
        d = union(d, sphere(x, y, z, (64.99998, 25, 25), 15))
    if n >= 4:
        d = sub(d, box(x, y, z, (20, -1, -1), (30, 2e-5, 51)))
    return d  # stage 6 moves it 1 km: drawn in the local frame


def acid11(x, y, z, n=6):
    base = union(box(x, y, z, (-80, -50, 0), (80, 50, 10)), cyl(x, y, z, "z", (-40, 0), 3, 9, 12),
                 cyl(x, y, z, "z", (13.5, 0), 3, 9, 12))
    ring = acid1(x + 40, y, z - 30, 3)
    cx, cy, cz = (x - 13.5) / 0.25, y / 0.25, (z - 30) / 0.25
    cam = sub(0.25 * acid2(cx, cy, cz, 3), cyl(x - 13.5, y, z - 30, "z", (0, 0), 1, -10, 40))
    return union(base, ring, cam)  # at the start of the motion (stages 4 to 6 turn it, with no new shape)


SKETCHES = {"acid-1": acid1, "acid-2": acid2, "acid-3": acid3, "acid-4": acid4, "acid-5": acid5, "acid-6": acid6,
            "acid-7": acid7, "acid-8": acid8, "acid-9": acid9, "acid-10": acid10, "acid-11": acid11}

# The box each stage's volume is sampled in (local frame) and the grid step.
VOLUME_BOX = {
    "acid-1": ((-38.2, -38.2, -2), (38.2, 38.2, 8.1), 0.12),
    "acid-2": ((-61, -36, -0.5), (61, 41, 25.5), 0.25),
    "acid-3": ((-0.5, -0.5, -0.5), (80.5, 60.5, 32.5), 0.2),
    "acid-4": ((-20.5, -20.5, -30.5), (41.5, 20.5, 40.5), 0.2),
    "acid-5": ((-0.2, -0.2, -0.2), (30.2, 20.2, 10.2), 0.06),
    "acid-6": ((-502, -502, -502), (502, 502, 502), 4.0),
    "acid-7": ((-101, -101, -1), (101, 101, 201), 0.6),
    "acid-8": ((-51, -51, -1), (51, 51, 124), 0.35),
    "acid-9": ((-29, -29, -1), (29, 29, 101), 0.25),
    "acid-10": ((-0.5, -0.5, -0.5), (80.5, 50.5, 70.5), 0.2),
}

NOTES = {
    "acid-1": "the R 0.8 rounds are rolling-ball blends that stop at the notch rather than run around it",
    "acid-2": "the spline outline is sampled; the R 3 round and the 3° draft are blends of distances",
    "acid-3": "the M6 thread is drawn as its plain Ø4.917 hole; the R 0.5 rounds stop at the notch",
    "acid-4": "exact except the R 2 rounds' corners, which are a rounded cube's",
    "acid-5": "the 0.00002 chamfer and 0.000025 round are far below a pixel and left out",
    "acid-6": "exact; the ring and needle are too thin to see whole, so the insets zoom in",
    "acid-7": "the R 0.5 round is a blend along the rim only, not around the six break-outs",
    "acid-8": "exact; a quarter is cut away to show the closed shell",
    "acid-9": "the twisted faces' distance is approximate; the R 1 rounds are rolling-ball blends",
    "acid-10": "the 0.00002 dips and groove are far below a pixel; drawn before the 1 km move",
    "acid-11": "the parts at the start of the motion; the ring and cam are acid-1 and acid-2 at stage 3",
}


def expected_volume(test, upto):
    """[lo, hi] for the total volume after stage upto, chained from values.py, or None where it does not pin it."""
    lo = hi = None
    other = 0.0  # a separate body's pinned volume (acid-4's cylinders)
    for n in range(1, upto + 1):
        e = values.TESTS[test][n]
        v = e.get("vol")
        if v and v[0] == "exact":
            lo = hi = v[1]
        elif v and v[0] == "window":
            lo, hi = v[1], v[2]
        elif v and v[0] == "delta" and lo is not None:
            lo, hi = lo + v[1], hi + v[2]
        elif e.get("solid_vol") and lo is not None and e.get("bodies", 1) > 1:
            other = e["solid_vol"][0]
        else:
            lo = hi = None
    return None if lo is None else (lo + other, hi + other)


def check_stage(test):
    """The latest stage whose total volume values.py pins down."""
    for n in range(len(values.TESTS[test]), 0, -1):
        if expected_volume(test, n):
            return n
    return None


def sampled_volume(f, lo, hi, h):
    xs = np.arange(lo[0] + h / 2, hi[0], h)
    ys = np.arange(lo[1] + h / 2, hi[1], h)
    zs = np.arange(lo[2] + h / 2, hi[2], h)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    gx, gy = gx.ravel(), gy.ravel()
    per = max(1, 3_000_000 // len(gx))
    total = 0.0
    for k in range(0, len(zs), per):
        zz = zs[k:k + per]
        X = np.tile(gx, len(zz))
        Y = np.tile(gy, len(zz))
        Z = np.repeat(zz, len(gx))
        total += np.clip(0.5 - f(X, Y, Z) / h, 0, 1).sum()
    return total * h ** 3


def main(args):
    no_vol = "--no-volume" in args
    tests = [a for a in args if a.startswith("acid-")] or list(SKETCHES)
    man_path = os.path.join(render.IMG, "sketches.json")
    man = json.load(open(man_path, encoding="utf-8")) if os.path.exists(man_path) else {}
    for t in tests:
        f = SKETCHES[t]
        t0 = time.time()
        final = len(values.TESTS[t])
        img = render.save_png(render.render_sdf(lambda x, y, z: f(x, y, z, final), render.VIEWS[t]),
                              os.path.join(render.IMG, "%s-sketch.png" % t))
        entry = {"image": os.path.basename(img), "stage": final, "note": NOTES[t]}
        n = check_stage(t)
        if n and not no_vol and t in VOLUME_BOX:
            lo, hi = expected_volume(t, n)
            v = sampled_volume(lambda x, y, z: f(x, y, z, n), *VOLUME_BOX[t])
            mid = (lo + hi) / 2
            out = max(lo - v, v - hi, 0) / mid
            entry.update(check_stage=n, expected=[lo, hi], volume=v, rel_err=abs(v - mid) / mid, outside_rel=out)
        elif t in man and "volume" in man[t]:
            entry.update({k: man[t][k] for k in ("check_stage", "expected", "volume", "rel_err", "outside_rel")
                          if k in man[t]})
        man[t] = entry
        msg = ""
        if "volume" in entry:
            msg = "stage %d volume %.1f, analytic %.1f-%.1f (%.2f%% from the middle)" % (
                entry["check_stage"], entry["volume"], entry["expected"][0], entry["expected"][1], 100 * entry["rel_err"])
        print("%s: %s %s (%.0f s)" % (t, os.path.relpath(img, ROOT), msg, time.time() - t0), flush=True)
    with open(man_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(dict(sorted(man.items(), key=lambda kv: int(kv[0].split("-")[1]))), fh, indent=1)
        fh.write("\n")


if __name__ == "__main__":
    main(sys.argv[1:])
