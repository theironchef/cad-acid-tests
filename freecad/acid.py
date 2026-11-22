"""Build one acid test in FreeCAD, stage by stage, and check each stage against expected/values.py.

Run inside freecadcmd (FreeCAD's public Python API, as a separate program). Only the core Part and
PartDesign modules are used: no add-ons and no other workbenches. Environment:
  AT_TEST   the test, e.g. acid-1
  AT_OUT    the results file: one JSON line is appended per stage, as soon as the stage is done
  AT_REPO   the repository root (to import expected/values.py)
tools/run_freecad.py starts this once per test and enforces the per-stage time limit.
"""
import json
import math
import os
import sys
import time
import traceback

import FreeCAD as App
import Part

sys.path.insert(0, os.path.join(os.environ["AT_REPO"], "expected"))
import values  # noqa: E402

V = App.Vector
TOL = 1e-3
TEST, OUT = os.environ["AT_TEST"], os.environ["AT_OUT"]
VERSION = ".".join(App.Version()[:3])


# ------------------------------------------------------------------ picking
def _dist(shape, pt):
    return shape.distToShape(Part.Vertex(V(*pt)))[0]


def edges_near(shape, pt, tol=TOL):
    found = [e for e in shape.Edges if _dist(e, pt) < tol]
    if not found:
        raise RuntimeError("no edge at %r" % (pt,))
    return found


def face_near(shape, pt, tol=TOL):
    best = min(shape.Faces, key=lambda f: _dist(f, pt))
    if _dist(best, pt) > tol:
        raise RuntimeError("no face at %r" % (pt,))
    return best


def loop_edges(face, pt, tol=TOL):
    """The edges of the face's wire that passes through pt."""
    for w in face.Wires:
        if any(_dist(e, pt) < tol for e in w.Edges):
            return list(w.Edges)
    raise RuntimeError("no loop of the face at %r" % (pt,))


# ------------------------------------------------------------------ operations, as a FreeCAD user does them
def big_box(z0, up=True, size=1e5):
    return Part.makeBox(2 * size, 2 * size, size, V(-size, -size, z0 if up else z0 - size))


def split(shape, point, normal, size=1e5):
    """Split by the plane through point with the given normal, keeping both sides (Part: common with
    each half-space)."""
    n = V(*normal)
    n.normalize()
    half = Part.makeBox(2 * size, 2 * size, size, V(-size, -size, 0))
    sols = []
    for sign in (1, -1):
        h = half.copy()
        h.Placement = App.Placement(V(*point), App.Rotation(V(0, 0, 1), n * sign))
        sols += shape.common(h).Solids
    return Part.makeCompound(sols)


def closed_shell(shape, t):
    """Hollow to wall t with no face removed: the shape minus its inward offset (Part Offset + Cut).
    Tries the arc join, then the intersection join, as a user would."""
    last = None
    for join in (0, 2):
        try:
            inner = shape.makeOffsetShape(-t, 1e-6, join=join)
            if inner.isNull() or not inner.Solids:
                raise RuntimeError("the offset is empty")
            return shape.cut(inner)
        except Exception as e:  # noqa: PERF203
            last = e
    raise last


def draft(shape, side_pt, neutral_pt, angle, outward=True):
    """PartDesign Draft on the face at side_pt, neutral plane at neutral_pt. Tries both directions and
    keeps the one that adds volume (outward) or removes it (inward), as a user would flip Reversed."""
    def index(f):
        return next(i for i, g in enumerate(shape.Faces) if g.isSame(f)) + 1

    i_side, i_neut = index(face_near(shape, side_pt)), index(face_near(shape, neutral_pt))
    v0 = shape.Volume
    doc = App.newDocument("draft")
    try:
        base = doc.addObject("Part::Feature", "Base")
        base.Shape = shape
        body = doc.addObject("PartDesign::Body", "Body")
        body.BaseFeature = base
        d = body.newObject("PartDesign::Draft", "Draft")
        d.Base = (base, ["Face%d" % i_side])
        d.NeutralPlane = (base, ["Face%d" % i_neut])
        d.Angle = angle
        status = ""
        for rev in (False, True):
            d.Reversed = rev
            doc.recompute()
            status = d.getStatusString()
            if not d.isValid() or d.Shape.isNull():
                continue
            s = d.Shape.copy()
            if (s.Volume > v0) == outward:
                return s
        raise RuntimeError("the draft did not build in the asked direction: %s" % status)
    finally:
        App.closeDocument(doc.Name)


def thread_cut(shape, centre, z0, length, r_minor, r_major, pitch):
    """A modelled internal thread: a 60-degree V swept along a helix (Part helix + pipe shell), cut."""
    helix = Part.makeHelix(pitch, length, r_minor)
    depth = r_major - (r_minor - 0.02)
    hw = depth * math.tan(math.radians(30))
    pts = [V(r_minor - 0.02, 0, -hw), V(r_major, 0, 0), V(r_minor - 0.02, 0, hw)]
    profile = Part.makePolygon(pts + [pts[0]])
    sweep = Part.Wire(helix).makePipeShell([profile], True, True)
    sweep.translate(V(centre[0], centre[1], z0))
    return shape.cut(sweep)


def cut_all(shape, tools):
    return shape.cut(tools[0].multiFuse(tools[1:]) if len(tools) > 1 else tools[0])


# ------------------------------------------------------------------ checks
def measure(shape):
    sols = shape.Solids
    valid = shape.isValid() and all(s.isValid() and all(sh.isClosed() for sh in s.Shells) for s in sols)
    return {"bodies": len(sols), "volume_mm3": shape.Volume, "valid": bool(valid)}


def inside(shape, pt):
    return any(s.isInside(V(*pt), 1e-7, True) for s in shape.Solids)


def check(stage, shape, prev_volume):
    exp = values.TESTS[TEST][stage]
    m = measure(shape)
    bad = []
    if not m["valid"]:
        bad.append("not a valid closed solid")
    if "bodies" in exp and m["bodies"] != exp["bodies"]:
        bad.append("%d bodies, expected %d" % (m["bodies"], exp["bodies"]))
    vol = exp.get("vol")
    if vol:
        v = m["volume_mm3"]
        slack = values.DELTA_SLACK_REL * abs(prev_volume or 0)
        if vol[0] == "exact" and abs(v - vol[1]) > values.EXACT_REL * max(1.0, abs(vol[1])):
            bad.append("volume %.6f, expected %.6f" % (v, vol[1]))
        elif vol[0] == "window" and not vol[1] <= v <= vol[2]:
            bad.append("volume %.6f outside [%.6f, %.6f]" % (v, vol[1], vol[2]))
        elif vol[0] == "delta" and prev_volume is not None and not vol[1] - slack <= v - prev_volume <= vol[2] + slack:
            bad.append("volume change %.6f outside [%.6f, %.6f]" % (v - prev_volume, vol[1], vol[2]))
    for sv in exp.get("solid_vol", []):
        if not any(abs(s.Volume - sv) <= values.EXACT_REL * max(1.0, sv) for s in shape.Solids):
            bad.append("no solid of volume %.6f (have %s)" % (sv, ", ".join("%.6f" % s.Volume for s in shape.Solids)))
    for p in exp.get("inside", []):
        if not inside(shape, p):
            bad.append("%r should be inside" % (tuple(round(c, 6) for c in p),))
    for p in exp.get("outside", []):
        if inside(shape, p):
            bad.append("%r should be outside" % (tuple(round(c, 6) for c in p),))
    return m, bad


def emit(rec):
    rec.update(pipeline="freecad", version="FreeCAD " + VERSION, test=TEST)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


# ------------------------------------------------------------------ the tests: each is a list of stage functions
def acid1():
    bx = math.sqrt(30 ** 2 - 0.95001 ** 2)
    return [
        lambda s: Part.makeTorus(30, 8).cut(big_box(0, up=False)),
        lambda s: s.fuse(Part.makeCylinder(0.95, 1.9, V(bx, 0.95001, -1.9))),
        lambda s: s.cut(Part.makeCylinder(0.7, 4, V(0, 38, -1))),
        lambda s: s.makeFillet(0.8, loop_edges(face_near(s, (-30, 0, 0)), (-38, 0, 0))
                               + loop_edges(face_near(s, (-30, 0, 0)), (-22, 0, 0))),
        lambda s: s.makeThickness([face_near(s, (bx, 0.95001, -1.9))], -0.6, 1e-3),
        lambda s: cut_all(s, [Part.makeCylinder(0.5, 40, V(30 * math.cos(math.radians(15 + 30 * k)),
                                                             30 * math.sin(math.radians(15 + 30 * k)), -20))
                              for k in range(12)]),
        lambda s: split(s, (0, 0, 4), (0, 0, 1)),
        lambda s: s.translated(V(10000, 0, 0)),
    ]


def spline_outline():
    """acid-2's outline, built exactly from its Bezier segments. FreeCAD's own periodic interpolation
    is only C1 at its first point, which is a different curve."""
    segs = values.SPLINE.bezier()
    poles, knots = [], [0.0]
    for i, seg in enumerate(segs):
        poles += [V(x, y, 0) for x, y in seg[:3]]
        knots.append(knots[-1] + values.SPLINE.h[i])
    poles.append(V(segs[0][0][0], segs[0][0][1], 0))
    c = Part.BSplineCurve()
    c.buildFromPolesMultsKnots(poles, [4] + [3] * (len(segs) - 1) + [4], knots, False, 3)
    return Part.Face(Part.Wire(c.toShape()))


def acid2():
    st = {}

    def s7(s):
        st["one"] = s
        return Part.makeCompound([s, s.mirror(V(80, 0, 0), V(1, 0, 0))])

    def s8(_):
        a = st["one"]
        return a.fuse([a.mirror(V(80, 0, 0), V(1, 0, 0)), Part.makeBox(50, 10, 5, V(55, -5, 0))])

    return [
        lambda s: spline_outline().extrude(V(0, 0, 25)),
        lambda s: s.makeFillet(3, loop_edges(face_near(s, (0, 0, 25)), (60, 0, 25), tol=1e-2)),
        lambda s: s.fuse([Part.makeCylinder(3, 10, V(x, y, 25)) for x in (-30, 30) for y in (-12, 12)]),
        lambda s: draft(s, (60, 0, 10), (0, 0, 0), 3, outward=True),
        lambda s: s.makeThickness([face_near(s, (0, 0, 0))], -1.5, 1e-3),
        lambda s: cut_all(s, [Part.makeCylinder(2, 50, V(x, y, -5)) for x in (-30, 30) for y in (-12, 12)]),
        s7, s8,
    ]


def acid3():
    return [
        lambda s: Part.makeBox(80, 60, 30),
        lambda s: s.fuse([Part.makeBox(3, 3, 2, V(20 + 20 * i - 1.5, 20 + 20 * j - 1.5, 30))
                          for i in range(3) for j in range(2)]),
        lambda s: thread_cut(s.cut(Part.makeCylinder(4.917 / 2, 20, V(40, 30, 10))),
                             (40, 30), 15, 15, 4.917 / 2, 3.0, 1.0),
        lambda s: cut_all(s, [Part.makeCylinder(1.25, 12, V(40 + 12 * math.cos(math.radians(45 * k)),
                                                              30 + 12 * math.sin(math.radians(45 * k)), 20))
                              for k in range(8)]),
        lambda s: s.cut(Part.makeCylinder(1.5, 6, V(1.50001, 0, 26))),
        lambda s: closed_shell(s, 1.2),
        lambda s: s.makeFillet(0.5, list(face_near(s, (40, 0.5, 30)).OuterWire.Edges)),
    ]


def acid4():
    st = {}

    def s4(s):
        cyl = Part.makeCylinder(5, 60, V(26, 0, -30)).fuse(Part.makeCylinder(5, 60, V(35.99999, 0, -30)))
        st["cube"], st["cyl"] = s, cyl
        return Part.makeCompound([s, cyl])

    def s5(_):
        st["cyl"] = st["cyl"].fuse(Part.makeSphere(5, V(26, 0, 34.99999)))
        return Part.makeCompound([st["cube"], st["cyl"]])

    def s6(_):
        cube = st["cube"]
        mids = [(x, y, 0) for x in (-20, 20) for y in (-20, 20)] + [(x, 0, z) for x in (-20, 20) for z in (-20, 20)] \
            + [(0, y, z) for y in (-20, 20) for z in (-20, 20)]
        return Part.makeCompound([cube.makeFillet(2, [edges_near(cube, p)[0] for p in mids]), st["cyl"]])

    return [
        lambda s: Part.makeBox(40, 40, 40, V(-20, -20, -20)),
        lambda s: cut_all(s, [Part.makeCylinder(9, 42, V(-21, 0, 0), V(1, 0, 0)),
                              Part.makeCylinder(9, 42, V(0, -21, 0), V(0, 1, 0)),
                              Part.makeCylinder(9, 42, V(0, 0, -21), V(0, 0, 1))]),
        lambda s: cut_all(s, [Part.makeCylinder(4, 42, V(-21, y, 0), V(1, 0, 0)) for y in (-10, 10)]
                          + [Part.makeCylinder(4, 42, V(x, -21, 1e-5), V(0, 1, 0)) for x in (-10, 10)]),
        s4, s5, s6,
    ]


def acid5():
    return [
        lambda s: Part.makeBox(30, 20, 10, V(1000, 1000, 1000)),
        lambda s: s.makeFillet(1.3, [edges_near(s, (x, y, 1005))[0] for x in (1000, 1030) for y in (1000, 1020)]),
        lambda s: s.makeFillet(1.29, loop_edges(face_near(s, (1015, 1010, 1010)), (1015, 1000, 1010))),
        lambda s: s.makeChamfer(0.00002, edges_near(s, (1015, 1000, 1000))),
        lambda s: s.makeFillet(0.000025, edges_near(s, (1000, 1010, 1000))),
        lambda s: s.makeThickness([face_near(s, (1015, 1010, 1000))], -0.4, 1e-6),
    ]


def acid6():
    z0 = 499.99975
    st = {}

    def s1(_):
        st["sph"] = Part.makeSphere(500).cut(big_box(z0))
        return st["sph"]

    def s2(_):
        st["sph"] = st["sph"].fuse(Part.makeCylinder(0.06, 0.12, V(0, 0, z0)))
        return st["sph"]

    def s3(_):
        st["ring"] = Part.makeTorus(500, 0.5).cut(big_box(0, up=False)).translated(V(0, 0, 600))
        return Part.makeCompound([st["sph"], st["ring"]])

    def s4(_):
        st["ring"] = closed_shell(st["ring"], 0.06)
        return Part.makeCompound([st["sph"]] + st["ring"].Solids)

    def s5(_):
        st["needle"] = draft(Part.makeCylinder(0.5, 1000, V(0, 0, -1600)), (0.5, 0, -1100), (0, 0, -1600),
                             0.02, outward=False)
        return Part.makeCompound([st["sph"]] + st["ring"].Solids + [st["needle"]])

    def s6(_):
        joined = st["sph"].fuse([Part.makeCylinder(0.4, 200, V(0, 0, -600)), st["needle"]])
        return Part.makeCompound(joined.Solids + st["ring"].Solids)

    return [s1, s2, s3, s4, s5, s6]


def acid7():
    return [
        lambda s: Part.makeCone(100, 40, 200),
        lambda s: cut_all(s, [Part.makeCylinder(5, 51, V(32 * math.cos(math.radians(60 * k)),
                                                          32 * math.sin(math.radians(60 * k)), 150))
                              for k in range(6)]),
        lambda s: draft(s, (70, 0, 100), (0, 0, 0), 18, outward=False),
        lambda s: s.makeFillet(0.5, list(face_near(s, (0, 0, 200)).OuterWire.Edges)),
        lambda s: s.cut(Part.makeCylinder(60, 81, V(0, 0, -1))),
        lambda s: s.makeThickness([face_near(s, (80, 0, 0))], -2, 1e-3),
    ]


def acid8():
    st = {}

    def ring(z):
        return [Part.makeCylinder(3, 20, V(50, 0, z), V(-1, 0, 0)).rotated(V(0, 0, 0), V(0, 0, 1), 15 * k)
                for k in range(24)]

    def s6(s):
        st["parts"] = split(s, (0, 0, 61), (0, 0, 1)).Solids
        return Part.makeCompound(st["parts"])

    def s7(_):
        upper = max(st["parts"], key=lambda p: p.BoundBox.ZMax)
        return Part.makeCompound(st["parts"] + [upper.mirror(V(0, 0, 140), V(0, 0, 1))])

    return [
        lambda s: Part.makeCylinder(50, 120),
        lambda s: cut_all(s, ring(20)),
        lambda s: cut_all(s, sum((ring(z) for z in (40, 60, 80, 100)), [])),
        lambda s: s.fuse([Part.makeCylinder(2, 3, V(30 * math.cos(math.radians(15 * k)),
                                                     30 * math.sin(math.radians(15 * k)), 120)) for k in range(7)]),
        lambda s: closed_shell(s, 2),
        s6, s7,
    ]


def acid9():
    def loft(_):
        def sq(z):
            return [V(x, y, z) for x, y in values.tower_section(z)]
        w0, w1 = Part.makePolygon(sq(0) + [sq(0)[0]]), Part.makePolygon(sq(100) + [sq(100)[0]])
        return Part.makeLoft([w0, w1], True, True)

    def round_sphere_edges(s):
        edges = []
        for e in s.Edges:
            kinds = {type(f.Surface).__name__ for f in s.ancestorsOfType(e, Part.Face)}
            if "Sphere" in kinds and kinds - {"Sphere", "Plane", "Cylinder"}:
                edges.append(e)
        if not edges:
            raise RuntimeError("no sphere-to-twisted-face edges found")
        return s.makeFillet(1, edges)

    return [
        loft,
        lambda s: s.cut(Part.makeCylinder(36.94 / 2, 102, V(0, 0, -1))),
        lambda s: s.common(Part.makeSphere(55, V(0, 0, 50))),
        round_sphere_edges,
        lambda s: s.makeThickness([face_near(s, (0, 22, 100))], -1, 1e-3),
        lambda s: split(s, (0, 0, 0), (1, -1, 0)),
    ]


def acid10():
    return [
        lambda s: Part.makeBox(50, 50, 50),
        lambda s: s.fuse(Part.makeCylinder(10, 50, V(0, 25, 59.99998), V(1, 0, 0))),
        lambda s: s.fuse(Part.makeSphere(15, V(64.99998, 25, 25))),
        lambda s: s.cut(Part.makeBox(10, 1.00002, 52, V(20, -1, -1))),
        lambda s: s.makeFillet(5, [edges_near(s, (x, y, 25))[0] for x in (0, 50) for y in (0, 50)]),
        lambda s: s.translated(V(1000000, 1000000, 0)),
    ]


def acid11():
    """The orrery, with FreeCAD's built-in Assembly workbench: revolute joints, a Gears joint, and a
    Simulation with an angular motion on the ring's joint, read back frame by frame."""
    import builtins
    import JointObject
    import UtilsAssembly
    try:
        from PySide import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide6 import QtCore, QtGui, QtWidgets
    # CommandCreateSimulation names Qt classes at import time, which it expects the GUI to provide
    builtins.QtCore, builtins.QtGui, builtins.QtWidgets = QtCore, QtGui, QtWidgets
    from CommandCreateSimulation import Motion, Simulation

    st = {}

    def build(fn, upto):
        shape = None
        for f in fn()[:upto]:
            shape = f(shape)
        return shape

    def placed():
        return Part.makeCompound([o.Shape for o in st["parts"]])

    def circle(obj, centre, radius):
        for i, e in enumerate(obj.Shape.Edges, 1):
            c = e.Curve
            if hasattr(c, "Radius") and abs(c.Radius - radius) < 1e-6 and (c.Center - V(*centre)).Length < 1e-6:
                return "Edge%d" % i
        raise RuntimeError("no circle of radius %g at %r" % (radius, centre))

    def joint(kind, a, ea, b, eb, name):
        j = st["joints"].newObject("App::FeaturePython", name)
        JointObject.Joint(j, JointObject.JointTypes.index(kind))
        j.Proxy.setJointConnectors(j, [[a, [ea, ea]], [b, [eb, eb]]])
        return j

    def zangle(obj):
        v = obj.Placement.Rotation.multVec(V(1, 0, 0))
        return math.degrees(math.atan2(v.y, v.x))

    def s1(_):
        doc = App.newDocument("orrery")
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        st.update(doc=doc, asm=asm, joints=asm.newObject("Assembly::JointGroup", "Joints"))
        base = asm.newObject("Part::Feature", "Base")
        base.Shape = Part.makeBox(160, 100, 10, V(-80, -50, 0)).fuse(
            [Part.makeCylinder(3, 2, V(-40, 0, 10)), Part.makeCylinder(3, 2, V(13.5, 0, 10))])
        g = st["joints"].newObject("App::FeaturePython", "Ground")
        JointObject.GroundedJoint(g, base)
        st.update(base=base, parts=[base])
        return placed()

    def s2(_):
        ring = st["asm"].newObject("Part::Feature", "Ring")
        ring.Shape = build(acid1, 3)
        ring.Placement = App.Placement(V(-40, 0, 30), App.Rotation())
        st["doc"].recompute()
        j = joint("Revolute", st["base"], circle(st["base"], (-40, 0, 12), 3), ring, circle(ring, (-40, 0, 30), 22), "J1")
        j.Offset1 = App.Placement(V(0, 0, 18), App.Rotation())
        st.update(ring=ring, j1=j)
        st["parts"].append(ring)
        if st["asm"].solve() != 0:
            raise RuntimeError("the assembly did not solve with the ring joint")
        return placed()

    def s3(_):
        cam = st["asm"].newObject("Part::Feature", "Cam")
        shape = build(acid2, 3)
        shape.scale(0.25, V(0, 0, 0))  # a uniform scale keeps the analytic surfaces (transformGeometry would not)
        cam.Shape = shape.cut(Part.makeCylinder(1, 50, V(0, 0, -10)))
        cam.Placement = App.Placement(V(13.5, 0, 30), App.Rotation())
        st["doc"].recompute()
        j = joint("Revolute", st["base"], circle(st["base"], (13.5, 0, 12), 3), cam, circle(cam, (13.5, 0, 30), 1), "J2")
        j.Offset1 = App.Placement(V(0, 0, 18), App.Rotation())
        st.update(cam=cam, j2=j)
        st["parts"].append(cam)
        if st["asm"].solve() != 0:
            raise RuntimeError("the assembly did not solve with the cam joint")
        return placed()

    def s4(_):
        g = joint("Gears", st["ring"], circle(st["ring"], (-40, 0, 30), 22), st["cam"], circle(st["cam"], (13.5, 0, 30), 1), "Gear")
        g.Distance = 3
        g.Distance2 = 1
        if st["asm"].solve() != 0:
            raise RuntimeError("the assembly did not solve with the gear")
        return placed()

    def s5(_):
        asm = st["asm"]
        sim = UtilsAssembly.getSimulationGroup(asm).newObject("App::FeaturePython", "Simulation")
        Simulation(sim)
        sim.bTimeEnd, sim.cTimeStepOutput = 0.35, 0.01
        motion = asm.newObject("App::FeaturePython", "Motion")
        Motion(motion, "Angular", st["j1"], "%.15f*time" % (math.radians(10) / 0.01))  # 10 degrees per frame
        sim.Group = sim.Group + [motion]
        st["doc"].recompute()
        asm.generateSimulation(sim)
        n = asm.numberOfFrames()
        exp = values.TESTS[TEST][5]["motion"]
        frames, bad = [], []
        for f in range(n):
            asm.updateForFrame(f)
            frames.append((f, zangle(st["ring"]), zangle(st["cam"])))
        angles = frames[-len(exp["steps"]):]
        st["frames"] = [f for f, _, _ in angles]
        for want, (f, r, c) in zip(exp["steps"], angles):
            dr = abs(((r - want + 180) % 360) - 180)
            dc = abs(((c - exp["ratio"] * r + 180) % 360) - 180)
            if dr > exp["tol_deg"] or dc > exp["tol_deg"]:
                bad.append("frame %d: ring %.9f (want %g), cam %.9f (want %.9f)" % (f, r, want, c, (exp["ratio"] * r) % 360))
                break
        if len(angles) < len(exp["steps"]):
            bad.append("%d frames, expected %d" % (len(angles), len(exp["steps"])))
        return placed(), bad

    def s6(_):
        asm, bad = st["asm"], []
        for f in st["frames"]:
            asm.updateForFrame(f)
            shapes = [(o.Name, o.Shape) for o in st["parts"]]
            for i in range(len(shapes)):
                for j in range(i + 1, len(shapes)):
                    v = shapes[i][1].common(shapes[j][1]).Volume
                    if v > 1e-9:
                        bad.append("frame %d: %s and %s overlap by %.3g mm3" % (f, shapes[i][0], shapes[j][0], v))
            if bad:
                break
        return placed(), bad

    return [s1, s2, s3, s4, s5, s6]


BUILDERS = {"acid-1": acid1, "acid-2": acid2, "acid-3": acid3, "acid-4": acid4, "acid-5": acid5,
            "acid-6": acid6, "acid-7": acid7, "acid-8": acid8, "acid-9": acid9, "acid-10": acid10,
            "acid-11": acid11}


def main():
    stages = BUILDERS[TEST]()
    shape, prev_v = None, None
    for n in range(1, len(values.TESTS[TEST]) + 1):
        with open(OUT + ".progress", "w") as f:
            f.write("%d %f\n" % (n, time.time()))
        t0 = time.time()
        extra = []
        try:
            shape = stages[n - 1](shape)
            if isinstance(shape, tuple):
                shape, extra = shape
        except Exception as e:  # FreeCAD raises Part.OCCError and others for a refused operation
            emit({"stage": n, "outcome": "error", "seconds": round(time.time() - t0, 3),
                  "detail": "%s: %s" % (type(e).__name__, str(e).strip()[:300])})
            break
        secs = round(time.time() - t0, 3)
        m, bad = check(n, shape, prev_v)
        bad += extra
        emit({"stage": n, "outcome": "pass" if not bad else "fail", "seconds": secs, "measured": m,
              "detail": "; ".join(bad)})
        prev_v = m["volume_mm3"]


main()
