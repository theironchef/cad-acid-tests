"""Expected values for every stage of the ten acid tests, worked out from the dimensions in TESTS.md.

Plain Python, no CAD program: each number is a closed form, a quadrature of an exact shape, or a
window bounded by simpler shapes. Every pipeline imports this file and checks its results against it.

A stage's checks (any may be absent):
  bodies      exact number of solids
  vol         ("exact", V)          total volume, to EXACT_REL relative
              ("delta", lo, hi)     the change from the previous stage's measured volume lies in [lo, hi],
                                    widened by DELTA_SLACK_REL x the previous total (measurement noise)
              ("window", lo, hi)    total volume lies in [lo, hi]
  solid_vol   [V, ...]              each value is the volume of some solid, to EXACT_REL relative
  inside      [(x, y, z), ...]      points that must be inside the result
  outside     [(x, y, z), ...]      points that must be outside it

Programs report volume by numerical integration. EXACT_REL and DELTA_SLACK_REL leave room for that,
not for a different shape: a wrong shape shows in the probes, the body count, or a much larger error.

python expected/values.py           prints the exact values
python expected/values.py --poles   prints acid-2's outline as Bezier segments (control points)
"""
import math
import sys
from math import pi, sqrt, tan, radians, cos, sin, atan2

EXACT_REL = 1e-5
DELTA_SLACK_REL = 1e-6
QR = 1 - pi / 4  # a round of radius r on a straight 90-degree edge removes QR * r**2 per unit length


# ------------------------------------------------------------------ geometry helpers
class PeriodicSpline:
    """The C2 periodic cubic through the points, parameterised by chord length (acid-2's outline)."""

    def __init__(self, pts):
        n = len(pts)
        self.p, self.h = pts, [math.dist(pts[(i + 1) % n], pts[i]) for i in range(n)]
        self.d = [self._slopes(k) for k in range(2)]

    def _slopes(self, k):
        n, p, h = len(self.p), self.p, self.h
        m = [[0.0] * n + [0.0] for _ in range(n)]
        for i in range(n):
            hp, hn = h[i - 1], h[i]
            m[i][(i - 1) % n] += hn
            m[i][i] += 2 * (hp + hn)
            m[i][(i + 1) % n] += hp
            m[i][n] = 3 * (hn * (p[i][k] - p[i - 1][k]) / hp + hp * (p[(i + 1) % n][k] - p[i][k]) / hn)
        for c in range(n):
            for r in range(n):
                if r != c:
                    f = m[r][c] / m[c][c]
                    m[r] = [x - f * y for x, y in zip(m[r], m[c])]
        return [m[i][n] / m[i][i] for i in range(n)]

    def at(self, i, t, deriv=False):
        n, h = len(self.p), self.h[i]
        if deriv:
            w = (6 * t * t - 6 * t, 3 * t * t - 4 * t + 1, -6 * t * t + 6 * t, 3 * t * t - 2 * t)
        else:
            w = (2 * t ** 3 - 3 * t * t + 1, t ** 3 - 2 * t * t + t, -2 * t ** 3 + 3 * t * t, t ** 3 - t * t)
        return [w[0] * self.p[i][k] + w[1] * h * self.d[k][i] + w[2] * self.p[(i + 1) % n][k]
                + w[3] * h * self.d[k][(i + 1) % n] for k in range(2)]

    def bezier(self):
        """Each segment as 4 Bezier control points; the segments join C2 at the given points."""
        n, out = len(self.p), []
        for i in range(n):
            p0, p1, h = self.p[i], self.p[(i + 1) % n], self.h[i]
            d0 = [h * self.d[k][i] for k in range(2)]
            d1 = [h * self.d[k][(i + 1) % n] for k in range(2)]
            out.append([list(p0), [p0[0] + d0[0] / 3, p0[1] + d0[1] / 3],
                        [p1[0] - d1[0] / 3, p1[1] - d1[1] / 3], list(p1)])
        return out

    def area_perimeter(self, steps=4000):
        """Green's theorem and arc length, by Simpson's rule on each segment."""
        area = per = 0.0
        for i in range(len(self.p)):
            fa, fp = [], []
            for j in range(steps + 1):
                t = j / steps
                (x, y), (dx, dy) = self.at(i, t), self.at(i, t, True)
                fa.append(x * dy - y * dx)
                fp.append(math.hypot(dx, dy))
            simp = lambda f: (f[0] + f[-1] + 4 * sum(f[1:-1:2]) + 2 * sum(f[2:-1:2])) / (3 * steps)
            area += simp(fa) / 2
            per += simp(fp)
        return area, per


def circles_overlap(R, r, d):
    """The overlap area of circles of radii R and r whose centres are d apart."""
    if d >= R + r:
        return 0.0
    if d <= abs(R - r):
        return pi * min(R, r) ** 2
    a = r * r * math.acos((d * d + r * r - R * R) / (2 * d * r))
    b = R * R * math.acos((d * d + R * R - r * r) / (2 * d * R))
    c = 0.5 * sqrt((-d + r + R) * (d + r - R) * (d - r + R) * (d + r + R))
    return a + b - c


def circle_lens(r, d):
    """The overlap area of two circles of radius r whose centres are d apart."""
    if d >= 2 * r:
        return 0.0
    return 2 * r * r * math.acos(d / (2 * r)) - d / 2 * sqrt(4 * r * r - d * d)


def _seg_circle(ax, ay, bx, by, r):
    """Signed area of the triangle (origin, a, b) intersected with the disc of radius r at the origin."""
    def sector(px, py, qx, qy):
        return 0.5 * r * r * atan2(px * qy - py * qx, px * qx + py * qy)
    dx, dy = bx - ax, by - ay
    A, B, C = dx * dx + dy * dy, 2 * (ax * dx + ay * dy), ax * ax + ay * ay - r * r
    disc = B * B - 4 * A * C
    ins_a, ins_b = C <= 0, bx * bx + by * by <= r * r
    if disc <= 0:
        return sector(ax, ay, bx, by)
    s = sqrt(disc)
    t1, t2 = (-B - s) / (2 * A), (-B + s) / (2 * A)
    if ins_a and ins_b:
        return 0.5 * (ax * by - ay * bx)
    if ins_a:
        px, py = ax + t2 * dx, ay + t2 * dy
        return 0.5 * (ax * py - ay * px) + sector(px, py, bx, by)
    if ins_b:
        px, py = ax + t1 * dx, ay + t1 * dy
        return sector(ax, ay, px, py) + 0.5 * (px * by - py * bx)
    if 0 < t1 < 1 and 0 < t2 < 1:
        p1x, p1y, p2x, p2y = ax + t1 * dx, ay + t1 * dy, ax + t2 * dx, ay + t2 * dy
        return sector(ax, ay, p1x, p1y) + 0.5 * (p1x * p2y - p1y * p2x) + sector(p2x, p2y, bx, by)
    return sector(ax, ay, bx, by)


def polygon_disc_area(poly, r):
    """The area of a convex polygon (centred anywhere) intersected with the disc of radius r at the origin."""
    n = len(poly)
    return abs(sum(_seg_circle(*poly[i], *poly[(i + 1) % n], r) for i in range(n)))


def gauss_legendre(f, a, b, n=48, pieces=40):
    """Composite Gauss-Legendre quadrature (nodes by Newton iteration)."""
    xs, ws = [], []
    for i in range(1, n + 1):
        x = cos(pi * (i - 0.25) / (n + 0.5))
        for _ in range(100):
            p0, p1 = 1.0, x
            for k in range(2, n + 1):
                p0, p1 = p1, ((2 * k - 1) * x * p1 - (k - 1) * p0) / k
            dp = n * (x * p1 - p0) / (x * x - 1)
            x -= p1 / dp
        xs.append(x)
        ws.append(2 / ((1 - x * x) * dp * dp))
    total, hw = 0.0, (b - a) / pieces
    for j in range(pieces):
        lo = a + j * hw
        total += sum(w * f(lo + (x + 1) * hw / 2) for x, w in zip(xs, ws)) * hw / 2
    return total


# ------------------------------------------------------------------ acid-1 Seamstress
_t1 = pi ** 2 * 30 * 64
_bx = sqrt(30 ** 2 - 0.95001 ** 2)
A1 = {
    1: dict(bodies=1, vol=("exact", _t1), outside=[(30, 0, -0.5)], inside=[(30, 0, 4)]),
    2: dict(bodies=1, vol=("exact", _t1 + pi * 0.95 ** 2 * 1.9), inside=[(_bx, 0.95001, -1)]),
    3: dict(bodies=1, vol=("delta", -pi * 0.7 ** 2 * 4, -1e-6), outside=[(0, 38.1, 0.5)], inside=[(0, 36.5, 0.5)]),
    4: dict(bodies=1, vol=("delta", -QR * 0.64 * 2 * pi * (38 + 22) * 1.5, -1e-6), outside=[(-37.9, 0, 0.05)]),
    5: dict(bodies=1, vol=("window", 3900, 4600), outside=[(-30, 0, 4)], inside=[(-30, 0, 7.7)]),
    6: dict(bodies=1, vol=("delta", -30, -3), outside=[(30 * cos(radians(15)), 30 * sin(radians(15)), 7.7)]),
    7: dict(bodies=2, vol=("delta", 0, 0), inside=[(-36.6, 0, 3.9), (-36.6, 0, 4.1)]),
    8: dict(bodies=2, vol=("delta", 0, 0), inside=[(10000 - 30, 0, 7.7)], outside=[(10000 - 30, 0, 4)]),
}

# ------------------------------------------------------------------ acid-2 Spline bracelet
SPLINE_PTS = [(60, 0), (42, 30), (0, 40), (-42, 30), (-60, 0), (-42, -28), (0, -35), (42, -28)]
SPLINE = PeriodicSpline(SPLINE_PTS)
SPLINE_AREA, SPLINE_PER = SPLINE.area_perimeter()
# Spline solids: volume is a +-0.5% window only, because programs integrate B-spline volumes coarsely
# (FreeCAD 1.1.4 reports this extrusion 0.15% low). The probes carry the exact checks.
A2 = {
    1: dict(bodies=1, vol=("window", 25 * SPLINE_AREA * 0.995, 25 * SPLINE_AREA * 1.005),
            inside=[(0, 0, 12.5), (59.9, 0, 12.5)], outside=[(60.1, 0, 12.5), (54.85, 16.9, 12.5)]),
    2: dict(bodies=1, outside=[(59.9, 0, 24.9)], inside=[(57, 0, 22)]),
    3: dict(bodies=1, inside=[(30, 12, 34), (-30, -12, 34)]),
    4: dict(bodies=1, inside=[(60.5, 0, 20)]),
    5: dict(bodies=1, outside=[(0, 0, 10)], inside=[(0, 0, 24), (30, 12, 34)]),
    6: dict(bodies=1, outside=[(30, 12, 30), (-30, -12, 24)]),
    7: dict(bodies=2, outside=[(130, 12, 30)], inside=[(130, 0, 24)]),
    8: dict(bodies=1, inside=[(80, 0, 2.5)]),
}

# ------------------------------------------------------------------ acid-3 Hollow clockwork
_m6_minor = 4.917 / 2
A3 = {
    1: dict(bodies=1, vol=("exact", 144000.0)),
    2: dict(bodies=1, vol=("exact", 144108.0), inside=[(20, 20, 31)]),
    3: dict(bodies=1, vol=("delta", -pi * 9 * 20, -pi * _m6_minor ** 2 * 20), outside=[(40, 30, 25), (40, 30, 12)]),
    4: dict(bodies=1, vol=("delta", -8 * pi * 1.25 ** 2 * 10 - 2 * pi * 1.25 ** 2 * 2, -8 * pi * 1.25 ** 2 * 10),
            outside=[(52, 30, 25)]),
    5: dict(bodies=1, vol=("delta", -pi * 1.5 ** 2 / 2 * 4, -pi * 1.5 ** 2 / 2 * 4),
            outside=[(1.5, 0.5, 29)], inside=[(0.005, 0.5, 29)]),
    6: dict(bodies=1, vol=("window", 17000, 24000), outside=[(10, 10, 10)], inside=[(0.6, 10, 10)]),
    7: dict(bodies=1, vol=("delta", -QR * 0.25 * 280 * 1.1, -1e-4), outside=[(79.95, 59.95, 29.95)]),
}

# ------------------------------------------------------------------ acid-4 Crossfire
_r = 9
_tri = 3 * pi * _r * _r * 40 - 3 * (16 / 3) * _r ** 3 + 8 * (2 - sqrt(2)) * _r ** 3
_cyls = 2 * pi * 25 * 60 - circle_lens(5, 9.99999) * 60
_sph_cap = pi * 1e-5 ** 2 * (3 * 5 - 1e-5) / 3
A4 = {
    1: dict(bodies=1, vol=("exact", 64000.0)),
    2: dict(bodies=1, vol=("exact", 64000.0 - _tri), outside=[(0, 0, 0), (15, 0, 0)]),
    3: dict(bodies=1, vol=("delta", -4 * pi * 16 * 40, -2000), outside=[(10, 10, 0), (15, 10, 0)]),
    4: dict(bodies=2, solid_vol=[_cyls], inside=[(30, 0, 0)]),
    5: dict(bodies=2, solid_vol=[_cyls + 4 / 3 * pi * 125 - _sph_cap], inside=[(26, 0, 39)]),
    6: dict(bodies=2, vol=("delta", -QR * 4 * 40 * 12, -QR * 4 * 36 * 12), outside=[(19.9, 19.9, 0)]),
}

# ------------------------------------------------------------------ acid-5 Corner cascade
_shell5 = 0.4 * (2 * 30 * 10 + 2 * 20 * 10 + 30 * 20)
A5 = {
    1: dict(bodies=1, vol=("exact", 6000.0)),
    2: dict(bodies=1, vol=("exact", 6000 - 4 * QR * 1.3 ** 2 * 10), outside=[(1000.05, 1000.05, 1005)]),
    3: dict(bodies=1, vol=("delta", -QR * 1.29 ** 2 * 100, -QR * 1.29 ** 2 * (2 * 27.4 + 2 * 17.4) * 0.8),
            outside=[(1015, 1000.05, 1009.95)]),
    4: dict(bodies=1, vol=("delta", -1e-3, 0)),
    5: dict(bodies=1, vol=("delta", -1e-3, 0)),
    6: dict(bodies=1, vol=("window", _shell5 * 0.85, _shell5 * 1.05),
            outside=[(1015, 1010, 1005)], inside=[(1015, 1019.8, 1005)]),
}

# ------------------------------------------------------------------ acid-6 Needle and planet
_z0 = 499.99975
_cap = pi * (500 - _z0) ** 2 * (3 * 500 - (500 - _z0)) / 3
_sph = 4 / 3 * pi * 500 ** 3 - _cap
_ring = pi ** 2 * 500 * 0.25
_r1 = 0.5 - 1000 * tan(radians(0.02))
_needle = pi * 1000 / 3 * (0.25 + 0.5 * _r1 + _r1 * _r1)
A6 = {
    1: dict(bodies=1, vol=("exact", _sph), outside=[(0, 0, 500.01)], inside=[(0, 0, 499.99)]),
    2: dict(bodies=1, inside=[(0, 0, 500.05)], outside=[(0.07, 0, 500.05)]),
    3: dict(bodies=2, solid_vol=[_ring], inside=[(500, 0, 600.25)]),
    4: dict(bodies=2, outside=[(500, 0, 600.25)], inside=[(500, 0, 600.47)]),
    5: dict(bodies=3, solid_vol=[_needle], inside=[(0, 0, -1100)], outside=[(0.3, 0, -700)]),
    6: dict(bodies=2, inside=[(0, 0, -550)]),
}

# ------------------------------------------------------------------ acid-7 Volcano
_cone = pi * 200 / 3 * (100 ** 2 + 100 * 40 + 40 ** 2)
_T18 = tan(radians(18))
_R7 = lambda z: 100 - z * _T18  # the drafted side's radius at height z
_drafted = gauss_legendre(lambda z: pi * _R7(z) ** 2, 0, 200)     - 6 * gauss_legendre(lambda z: circles_overlap(_R7(z), 5, 32), 150, 200)
A7 = {
    1: dict(bodies=1, vol=("exact", _cone)),
    2: dict(bodies=1, vol=("exact", _cone - 6 * pi * 25 * 50), outside=[(32, 0, 190)]),
    3: dict(bodies=1, vol=("exact", _drafted), outside=[(36, 0, 195), (36, 0, 199)],
            inside=[(30, 10, 100), (35.5, 0, 140)]),
    4: dict(bodies=1, vol=("delta", -0.5, -1e-5), outside=[(35.0, 4.95, 199.95)]),
    5: dict(bodies=1, outside=[(0, 0, 40)]),
    6: dict(bodies=1, outside=[(0, 0, 150)], inside=[(0, 0, 199)]),
}

# ------------------------------------------------------------------ acid-8 Lattice drum
_hole = pi * 9 * 20
A8 = {
    1: dict(bodies=1, vol=("exact", pi * 50 ** 2 * 120)),
    2: dict(bodies=1, vol=("delta", -24 * _hole, -24 * _hole * 0.99), outside=[(40, 0, 20)]),
    3: dict(bodies=1, vol=("delta", -4 * 24 * _hole, -4 * 24 * _hole * 0.99), outside=[(40, 0, 100)]),
    4: dict(bodies=1, vol=("delta", 7 * pi * 4 * 3, 7 * pi * 4 * 3), inside=[(30, 0, 122)]),
    5: dict(bodies=1, outside=[(0, 0, 60)], inside=[(49, 0, 50)]),
    6: dict(bodies=2, vol=("delta", 0, 0)),
    7: dict(bodies=3),
}


# ------------------------------------------------------------------ acid-9 Twisted tower
def tower_section(z):
    """The loft's cross-section at height z: the square whose corners move straight from the
    bottom square's corners to the top square's corners, 45 degrees further around."""
    t = z / 100
    out = []
    for i in range(4):
        a = radians(45 + 90 * i)
        b = radians(90 + 90 * i)
        ra = 20 * sqrt(2)
        out.append(((1 - t) * ra * cos(a) + t * ra * cos(b), (1 - t) * ra * sin(a) + t * ra * sin(b)))
    return out


def _shoelace(p):
    return abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))) / 2


_bore9 = 36.94 / 2
_loft = gauss_legendre(lambda z: _shoelace(tower_section(z)), 0, 100)
_cut9 = _loft - pi * _bore9 ** 2 * 100
_sphere9 = gauss_legendre(lambda z: polygon_disc_area(tower_section(z), sqrt(55 ** 2 - (z - 50) ** 2)), 0, 100) \
    - pi * _bore9 ** 2 * 100
_mid = tower_section(50)
_mid_in = sqrt(_mid[0][0] ** 2 + _mid[0][1] ** 2) / sqrt(2)


def _along(z, rad):
    """A point at height z on the ray toward the section's first corner, at radius rad."""
    c = tower_section(z)[0]
    ang = atan2(c[1], c[0])
    return (rad * cos(ang), rad * sin(ang), z)


A9 = {
    1: dict(bodies=1, vol=("exact", _loft), inside=[_along(10, 23), _along(50, 26)], outside=[_along(50, 26.3)]),
    2: dict(bodies=1, vol=("exact", _cut9), outside=[(0, 0, 50), (0, 18.4, 50)], inside=[_along(10, 23)]),
    3: dict(bodies=1, vol=("exact", _sphere9), outside=[(19.9, 19.9, 0.1)], inside=[_along(10, 23)]),
    4: dict(bodies=1, vol=("delta", -60, -0.05)),
    5: dict(bodies=1, outside=[_along(10, 23)], inside=[_along(10, 27)]),
    6: dict(bodies=2, vol=("delta", 0, 0)),
}

# ------------------------------------------------------------------ acid-10 Near misses
_seg = 10 * 10 * math.acos(1 - 1e-5 / 10) - (10 - 1e-5) * sqrt(2 * 10 * 1e-5 - 1e-10)
_cap10 = pi * 1e-5 ** 2 * (3 * 15 - 1e-5) / 3
_F = (1e6, 1e6, 0)
A10 = {
    1: dict(bodies=1, vol=("exact", 125000.0)),
    2: dict(bodies=1, vol=("exact", 125000 + pi * 100 * 50 - _seg * 50), inside=[(25, 25, 55), (25, 25, 50.000005)]),
    3: dict(bodies=1, vol=("exact", 125000 + pi * 100 * 50 - _seg * 50 + 4 / 3 * pi * 15 ** 3 - _cap10),
            inside=[(79, 25, 25), (50.000005, 25, 25)]),
    4: dict(bodies=1, outside=[(25, 0.000005, 25)], inside=[(25, 0.00002, 25), (15, 0.000005, 25)]),
    5: dict(bodies=1, vol=("delta", -4 * QR * 25 * 50, -4 * QR * 25 * 50), outside=[(0.1, 0.1, 25)]),
    6: dict(bodies=1, vol=("delta", 0, 0),
            outside=[(_F[0] + 25, _F[1] + 0.000005, 25), (_F[0] + 0.1, _F[1] + 0.1, 25)],
            inside=[(_F[0] + 25, _F[1] + 0.00002, 25), (_F[0] + 79, _F[1] + 25, 25)]),
}

TESTS = {"acid-1": A1, "acid-2": A2, "acid-3": A3, "acid-4": A4, "acid-5": A5,
         "acid-6": A6, "acid-7": A7, "acid-8": A8, "acid-9": A9, "acid-10": A10}
NAMES = {"acid-1": "Seamstress", "acid-2": "Spline bracelet", "acid-3": "Hollow clockwork", "acid-4": "Crossfire",
         "acid-5": "Corner cascade", "acid-6": "Needle and planet", "acid-7": "Volcano", "acid-8": "Lattice drum",
         "acid-9": "Twisted tower", "acid-10": "Near misses"}

if __name__ == "__main__":
    if "--poles" in sys.argv:
        for i, seg in enumerate(SPLINE.bezier()):
            print("segment %d: %s" % (i, ", ".join("(%.12f, %.12f)" % tuple(p) for p in seg)))
        sys.exit()
    print("acid-2 outline area %.9f perimeter %.9f; acid-9 mid-height half-width %.6f" % (SPLINE_AREA, SPLINE_PER, _mid_in))
    for name, stages in TESTS.items():
        for n, c in stages.items():
            if "vol" in c and c["vol"][0] == "exact":
                print("%s stage %d  V = %.6f" % (name, n, c["vol"][1]))
