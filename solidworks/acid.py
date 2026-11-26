"""Build one acid test in SOLIDWORKS, stage by stage, and check each stage against expected/values.py.

Runs as its own Python process (any Python with pywin32) and drives the running SOLIDWORKS through its COM API, late
bound. Each model is built as a SOLIDWORKS user builds it, from core part features only: sketches, extrude, revolve,
cut, fillet, chamfer, shell, draft, loft, helix and swept cut, feature patterns, Split, Move/Copy Body, Mirror,
Combine and Scale; acid-11 is a real assembly with standard and mechanical mates (concentric, distance, gear) driven
by an angle mate stepped through the API, checked with the core Interference Detection. No add-ins (no Toolbox,
SOLIDWORKS Motion or Simulation), and no model is built from temporary IModeler bodies: temporary bodies are used for
measuring only. Environment:
  AT_TEST   the test, e.g. acid-1
  AT_OUT    the results file: one JSON line is appended per stage, as soon as the stage is done
  AT_REPO   the repository root (to import expected/values.py)
tools/run_solidworks.py starts this once per test and enforces the per-stage time limit.
python solidworks/acid.py --version prints the version; --selftest validates the probe and the interference check.

Picks are by point: the face or edge nearest to a point on it (IFace2 / IEdge.GetClosestPointOn), as in
freecad/acid.py; the template's own planes are found by their normals. A feature SOLIDWORKS refuses (the API makes
none) or builds with an error (IFeature.GetErrorCode2, or a rebuild error) is an "error", with SOLIDWORKS' own
message or swFeatureError_e name.

Measurement:
  bodies   the solid bodies (IPartDoc.GetBodies2; in acid-11, every component's IComponent2.GetBodies3)
  valid    every body passes IBody2.Check3 (Parasolid's body check) with no fault; a fault is named in the detail
  volume   the total of IMassProperty2 at AccuracyLevel Higher, one body at a time
  probes   a 0.0002 mm cube at the point, intersected with a temporary copy of each body (IBody2.Operations2). When
           no surface crosses the cube (overlap 0 or 1), that decides. Otherwise a surface is within 0.00017 mm, and
           the nearest face decides: with q its closest point to p, and p - q along the face's outward normal there
           (q inside the face, not on an edge), p is inside when p - q points against the normal. That is exact at
           any distance, which the cube's overlap alone is not: SOLIDWORKS integrates a tiny body with a curved face
           to only a few percent (IBody2.GetMassProperties), against a 2.5 % margin for a point 0.000005 mm from a
           surface. When the closest point is on an edge, the cube's overlap of at least half decides, as in
           solidcraft/acid.py. --selftest checks 62 points 0.000005 mm inside and outside the faces of a block (at
           a convex edge too), a hole, cylinders at 0, 1 m and 100 m, a sphere and a torus; the cube alone gets 7 of
           them wrong, the rule above none.

Conventions translated (each measured on SOLIDWORKS 2026 SP4.1):
  draft    the neutral-plane draft sets the face to the angle from the pull direction (absolute), whatever its taper:
           on the acid-7 cone (16.70 deg), 18 deg gives a top radius of 35.016 = 100 - 200 tan 18 deg, and 1 deg gives
           96.51, not the 36.17 an added angle would. So TESTS.md's angles are passed as they are. The direction flag
           is tried both ways and the one that adds (outward) or removes (inward) volume is kept, as freecad/acid.py
           does.
  spline   acid-2's outline is the periodic cubic B-spline with simple knots at the chord-length parameters, whose
           control points are read off values.SPLINE.bezier() (ISketchManager.CreateSplinesByEqnParams2); the sketch
           curve is checked against values.SPLINE to 1e-9 mm before it is used.
  seam     a revolved face's parameter seam lies on +X when the revolve axis points +Z, on -X when it points -Z: acid-1's
           centerline is drawn downward, so the seam is TESTS.md's 0-degree seam (y = 0, x > 0), next to the boss.
  thread   a modelled M6 x 1: a right-handed helix of pitch 1 on the hole's minor circle over the top 15, and a swept
           cut of the 60-degree V profile freecad/acid.py uses.
  join     Merge result on the feature, which joins it to the bodies it touches; a separate body is a separate feature
           with Merge result off. A hole "through" a body is a Through All cut.
  rounds   tangent propagation off, with every edge listed (TESTS.md names the edges or loops).
  split    Split (PreSplitBody2 / PostSplitBody2) by a reference plane, keeping every body.
  mirror   Mirror with bodies to mirror, merge off, about a reference plane.
  shell    a shell with no face removed hollows the body selected for it (in a multibody part SOLIDWORKS asks for
           the body to shell).
  acid-11  base, ring and cam are parts saved to %TEMP%\\cad-acid-tests-sw; the ring and the cam are the acid-1 and
           acid-2 stage functions run to stage 3 (the cam then scaled by the Scale feature and given its axle hole).
           Each joint is a concentric mate to a pin plus a 20 mm distance mate to the plate; a gear mate 3 : 1 couples
           ring and cam; an angle mate between the ring's and the assembly's right planes drives the ring. At every
           step the angles are read from the component transforms (IComponent2.Transform2), and the interference
           check runs over every pair of components, with touching counted as interference.
"""
import json
import math
import os
import re
import sys
import tempfile
import time
import traceback
import types

import pythoncom
import win32com.client
import win32com.client.dynamic
from win32com.client import VARIANT

REPO = os.environ.get("AT_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "expected"))
import values  # noqa: E402

TOL = 1e-3          # pick tolerance (mm), as freecad/acid.py
PROBE = 0.0002      # probe cube edge (mm), as solidcraft/acid.py
TEST, OUT = os.environ.get("AT_TEST"), os.environ.get("AT_OUT")
TMP = os.path.join(tempfile.gettempdir(), "cad-acid-tests-sw")
SES = None          # the Session, made by main()
VERSION = None

# ------------------------------------------------------------------ SOLIDWORKS API constants (swconst, SOLIDWORKS 2026)
swDocPART, swDocASSEMBLY = 1, 2
swDefaultTemplatePart, swDefaultTemplateAssembly = 8, 9
swUnitSystem, swUnitSystem_MMGS, swUnitsLinearDecimalPlaces = 263, 5, 49
swEndCondBlind, swEndCondThroughAll, swEndCondMidPlane = 0, 1, 6
SWBODYINTERSECT, SWBODYCUT, SWBODYADD = 15901, 15902, 15903
swRefPlaneDistance, swRefPlaneAngle, swRefPlaneCoincident, swRefPlaneFlip = 8, 16, 4, 256
swFmFillet = 1
swConstRadiusFillet = 0
swChamferDistanceDistance = 2
swFacePropNone = 0
swHelixDefinedByPitchAndRevolution = 0
swTwistControlFollowPath = 0
swScaleAboutOrigin = 1
swSolidBody = 0
swMassPropertyAccuracyLevel_Higher = 2
swMateCONCENTRIC, swMateDISTANCE, swMateANGLE, swMateGEAR = 1, 5, 6, 10
swMateAlignCLOSEST = 2
swAddMateError_NoError = 1
swSetValue_InAllConfigurations = 2
swBodyOperationNoIntersect = 1067
MARK_PROFILE, MARK_PATH, MARK_SEED, MARK_AXIS = 1, 4, 4, 1
MARK_MIRROR_PLANE, MARK_MIRROR_BODY = 2, 256
NULLDISP = VARIANT(pythoncom.VT_DISPATCH, None)


class SWError(Exception):
    """SOLIDWORKS refused a feature, or built it with an error: the stage is an "error" and the test stops."""


# ------------------------------------------------------------------ late-bound COM
def connect():
    """The running SOLIDWORKS (never starts one)."""
    o = pythoncom.GetActiveObject("SldWorks.Application")
    return win32com.client.dynamic.Dispatch(o.QueryInterface(pythoncom.IID_IDispatch))


def g(obj, name, *args):
    """A member by name: late bound, SOLIDWORKS' zero-argument methods appear as properties."""
    v = getattr(obj, name)
    return v(*args) if isinstance(v, types.MethodType) else v


def method(obj, name):
    """A method of an object without type information (IMathUtility, IMathPoint, IModeler, ISplineParamData)."""
    try:
        obj._FlagAsMethod(name)
    except Exception:
        pass
    return getattr(obj, name)


def safe(f, default=None):
    try:
        return f()
    except Exception:
        return default


def darr(vals):
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in vals])


def oarr(objs):
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, list(objs))


def is_feature(obj):
    return safe(lambda: g(obj, "GetTypeName2")) is not None


def dist(p, q):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(p, q)))


def polar(r, deg, z):
    return (r * math.cos(math.radians(deg)), r * math.sin(math.radians(deg)), z)


def sw_version(sw):
    """'SOLIDWORKS 2026 SP4.1' from ISldWorks.RevisionNumber ('34.4.1': major 34 is 2026), checked against the build
    ISldWorks.GetBuildNumbers2 names ('sw2026_SP04')."""
    rev = str(g(sw, "RevisionNumber"))
    major, sp, sub = (int(x) for x in rev.split(".")[:3])
    year = 1992 + major
    refs = [VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "") for _ in range(3)]
    sw.GetBuildNumbers2(*refs)
    m = re.match(r"sw(\d{4})_SP0*(\d+)", refs[0].value or "")
    if not m or int(m.group(1)) != year or int(m.group(2)) != sp:
        raise RuntimeError("RevisionNumber %s does not match the build %r" % (rev, refs[0].value))
    return "SOLIDWORKS %d SP%d.%d" % (year, sp, sub)


def scrub(text):
    """Nothing about this machine in a result: absolute paths become their last part, and the user and host names
    are replaced."""
    text = re.sub(r"[A-Za-z]:[\\/][^\s\"'<>|;,]*[\\/]([^\\/\s\"'<>|;,]+)", r"\1", str(text))
    for var in ("USERNAME", "COMPUTERNAME", "USERDOMAIN", "USERPROFILE"):
        v = os.environ.get(var)
        if v and len(v) > 2:
            text = re.sub(re.escape(v), "<%s>" % var.lower(), text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------ what SOLIDWORKS says when it refuses
def sw_messages(sw):
    """ISldWorks.GetErrorMessages: the session's pending messages (cleared once read), without the document
    headers ('Part3 - Rebuild Errors')."""
    refs = [VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None) for _ in range(3)]
    try:
        sw.GetErrorMessages(*refs)
    except Exception:
        return []
    texts = refs[0].value
    texts = [texts] if isinstance(texts, str) else list(texts or [])
    lines = [x.strip() for m in texts for x in (m or "").replace("\r\n", "\n").split("\n") if x.strip()]
    return [x for x in lines if not re.search(r" - Rebuild (Errors|Warnings)$", x)]


def create_errors(fm):
    """IFeatureManager.GetCreateFeatureErrors: why the last feature creation failed."""
    for kinds in ((pythoncom.VT_VARIANT, pythoncom.VT_BSTR), (pythoncom.VT_VARIANT, pythoncom.VT_VARIANT)):
        refs = [VARIANT(pythoncom.VT_BYREF | k, "" if k == pythoncom.VT_BSTR else None) for k in kinds]
        try:
            fm.GetCreateFeatureErrors(*refs)
        except Exception:
            continue
        msgs = refs[0].value
        return [str(m) for m in ([msgs] if isinstance(msgs, str) else list(msgs or [])) if m]
    return []


_ENUMS = {}


def enum_name(sw, enum, code):
    """The name of a swconst enumeration member (swFeatureError_e, ...) for a code, from SOLIDWORKS' own
    swconst.tlb."""
    if enum not in _ENUMS:
        names = {}
        try:
            tlb = pythoncom.LoadTypeLib(os.path.join(g(sw, "GetExecutablePath"), "swconst.tlb"))
            for i in range(tlb.GetTypeInfoCount()):
                if tlb.GetDocumentation(i)[0] == enum:
                    ti = tlb.GetTypeInfo(i)
                    for j in range(ti.GetTypeAttr().cVars):
                        vd = ti.GetVarDesc(j)
                        names[vd.value] = ti.GetDocumentation(vd.memid)[0]
        except Exception:
            pass
        _ENUMS[enum] = names
    return _ENUMS[enum].get(code, "%s %s" % (enum, code))


def error_code(f):
    """IFeature.GetErrorCode2: (code, is a warning)."""
    iw = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
    r = safe(lambda: f.GetErrorCode2(iw), 0)
    if isinstance(r, tuple):
        return r[0] or 0, bool(r[1])
    return r or 0, bool(iw.value)


# ------------------------------------------------------------------ the session and its documents
_PROGRESS = [1, time.time()]


def progress(stage=None):
    """The progress file the runner reads: '<stage> <start time>' on its first line, then the titles of the
    documents this process opened (the runner may close SOLIDWORKS after a hang only if no other is loaded)."""
    if stage is not None:
        _PROGRESS[:] = [stage, time.time()]
    if OUT:
        titles = [safe(lambda: g(d, "GetTitle"), "") for d in (SES.mine if SES else [])]
        with open(OUT + ".progress", "w", encoding="utf-8") as f:
            f.write("%d %f\n%s\n" % (_PROGRESS[0], _PROGRESS[1], json.dumps(titles)))


class Session:
    """The running SOLIDWORKS, and the documents this process opened (the only ones it closes)."""

    def __init__(self):
        self.sw = connect()
        self.mu = g(self.sw, "GetMathUtility")
        self.modeler = g(self.sw, "GetModeler")
        self.mine = []

    def opened(self, doc):
        self.mine.append(doc)
        progress()
        return doc

    def close_all(self):
        for d in reversed(self.mine):
            t = safe(lambda: g(d, "GetTitle"))
            if t:
                safe(lambda: self.sw.QuitDoc(t))
        self.mine = []


class Sketch:
    """A sketch on a plane feature or a planar face. Every point is given in model coordinates (mm), mapped to the
    sketch through ISketch.ModelToSketchTransform and checked to lie on the sketch plane, so no plane's orientation
    is hand-coded."""

    def __init__(self, part, base, name):
        self.p, self.name = part, name
        d = part.doc
        d.ClearSelection2(True)
        ok = base.Select2(False, 0) if is_feature(base) else part.select([base], 0)
        if not ok:
            raise RuntimeError("%s: cannot select the sketch plane" % name)
        part.skm.InsertSketch(True)
        self.sk = g(d, "GetActiveSketch2")
        if self.sk is None:
            raise RuntimeError("%s: no active sketch" % name)
        self.xf = self.sk.ModelToSketchTransform
        part.skm.AddToDB = True                     # no snapping, inferencing or automatic relations
        part.skm.DisplayWhenAdded = False

    def m(self, q):
        """Model point (mm) -> sketch coordinates (m)."""
        pt = method(self.p.ses.mu, "CreatePoint")(darr([v / 1000.0 for v in q]))
        s = method(pt, "MultiplyTransform")(self.xf).ArrayData
        if abs(s[2]) > 1e-11:
            raise RuntimeError("%s: %r is %.3g mm off the sketch plane" % (self.name, q, s[2] * 1000))
        return s[0], s[1]

    def _ok(self, seg, what):
        if seg is None:
            raise RuntimeError("%s: could not create %s" % (self.name, what))
        return seg

    def line(self, a, b, construction=False):
        (x1, y1), (x2, y2) = self.m(a), self.m(b)
        skm = self.p.skm
        seg = (skm.CreateCenterLine if construction else skm.CreateLine)(x1, y1, 0.0, x2, y2, 0.0)
        return self._ok(seg, "line %r-%r" % (a, b))

    def polygon(self, pts):
        return [self.line(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]

    def rect(self, a, b, z):
        return self.polygon([(a[0], a[1], z), (b[0], a[1], z), (b[0], b[1], z), (a[0], b[1], z)])

    def circle(self, c, r):
        x, y = self.m(c)
        return self._ok(self.p.skm.CreateCircleByRadius(x, y, 0.0, r / 1000.0), "circle %r r %g" % (c, r))

    def arc(self, c, a, b, via):
        """The arc about c from a to b that passes through via."""
        sc, sa, sb, sv = self.m(c), self.m(a), self.m(b), self.m(via)
        ang = lambda p: math.atan2(p[1] - sc[1], p[0] - sc[0])
        two = 2 * math.pi
        d = 1 if (ang(sv) - ang(sa)) % two < (ang(sb) - ang(sa)) % two else -1
        return self._ok(self.p.skm.CreateArc(sc[0], sc[1], 0.0, sa[0], sa[1], 0.0, sb[0], sb[1], 0.0, d),
                        "arc %r %r-%r" % (c, a, b))

    def bspline(self, poles, knots, periodic):
        """A cubic B-spline from its control points (model mm) and knots: ISketchManager.CreateSplinesByEqnParams2
        (ISplineParamData: Dimension 3, three coordinates per control point, non-rational; Order 4)."""
        sp = g(self.p.skm, "CreateSplineParamData")
        sp.Dimension = 3
        sp.Order = 4
        sp.Periodic = 1 if periodic else 0
        sp.ControlPointsCount = len(poles)
        flat = []
        for q in poles:
            flat += list(self.m(q)) + [0.0]
        ok1 = method(sp, "SetControlPoints")(darr(flat))
        ok2 = method(sp, "SetKnotPoints")(darr(knots))
        segs = self.p.skm.CreateSplinesByEqnParams2(sp)
        if not (ok1 and ok2) or not segs:
            raise RuntimeError("%s: the B-spline was not created" % self.name)
        return list(segs)

    def done(self):
        skm = self.p.skm
        skm.AddToDB = False
        skm.DisplayWhenAdded = True
        skm.InsertSketch(True)
        f = self.p.last()
        if f is None or g(f, "GetTypeName2") != "ProfileFeature":
            raise RuntimeError("%s: the sketch did not close" % self.name)
        f.Name = "Sketch-" + self.name
        return f


def plane_normal(f):
    """Unit normal of a reference plane feature (IRefPlane.Transform: the plane's z axis)."""
    a = list(g(g(f, "GetSpecificFeature2"), "Transform").ArrayData)
    return a[6:9]


def principal_planes(first):
    """The template's three planes, by their normals: {'x': Right, 'y': Top, 'z': Front}."""
    planes = {}
    f = first
    while f is not None and len(planes) < 3:
        if g(f, "GetTypeName2") == "RefPlane":
            n = plane_normal(f)
            planes["xyz"[max(range(3), key=lambda i: abs(n[i]))]] = f
        f = g(f, "GetNextFeature")
    if sorted(planes) != ["x", "y", "z"]:
        raise RuntimeError("the template's planes are not the three principal planes")
    return planes


class Doc:
    """A new document of this process: MMGS units, no What's Wrong pop-up."""

    def _new(self, ses, template_pref, doc_type):
        self.ses, self.sw = ses, ses.sw
        tpl = self.sw.GetUserPreferenceStringValue(template_pref)
        if not tpl or not os.path.exists(tpl):
            raise RuntimeError("SOLIDWORKS has no default template for this document type")
        self.doc = self.sw.NewDocument(tpl, 0, 0.0, 0.0)
        if self.doc is None or g(self.doc, "GetType") != doc_type:
            raise RuntimeError("NewDocument made no document")
        ses.opened(self.doc)
        self.doc.ShowFeatureErrorDialog = False      # a document property: no pop-up on a rebuild error
        ext = self.doc.Extension
        ok = ext.SetUserPreferenceInteger(swUnitSystem, 0, swUnitSystem_MMGS) and \
            ext.SetUserPreferenceInteger(swUnitsLinearDecimalPlaces, 0, 8)
        if not ok:
            raise RuntimeError("could not set the document units")
        self.ext, self.fm = ext, self.doc.FeatureManager
        pl = principal_planes(g(self.doc, "FirstFeature"))
        self.front, self.top, self.right = pl["z"], pl["y"], pl["x"]

    def last(self):
        return self.doc.FeatureByPositionReverse(0)

    def select(self, objs, mark=0, append=False):
        """Select entities (faces, edges, bodies) with a mark, and features with Select2."""
        sm = g(self.doc, "SelectionManager")
        if not append:
            self.doc.ClearSelection2(True)
        n0 = g(sm, "GetSelectedObjectCount2", -1) or 0
        ok, ents = True, []
        for o in objs:
            if is_feature(o):
                ok = ok and o.Select2(True, mark)
            else:
                ents.append(o)
        if ents:
            sd = g(sm, "CreateSelectData")
            sd.Mark = mark
            self.ext.MultiSelect2(oarr(ents), True, sd)
        return bool(ok) and (g(sm, "GetSelectedObjectCount2", -1) or 0) == n0 + len(objs)

    def sel(self, objs, mark=0, append=False):
        if not self.select(objs, mark, append):
            raise RuntimeError("cannot select %d entities" % len(objs))

    def feature(self, call, what):
        """Run the API call that makes one feature and return the feature. SWError when SOLIDWORKS refuses it (no
        feature, with the reasons it gives) or builds it with an error (swFeatureError_e and its message)."""
        before = safe(lambda: self.last().Name)
        sw_messages(self.sw)
        f = call()
        if f is None or not is_feature(f):
            last = self.last()
            f = last if last is not None and last.Name != before else None
        self.doc.ClearSelection2(True)
        msgs = sw_messages(self.sw)
        if f is None:
            why = create_errors(self.fm) + msgs
            raise SWError(scrub("%s: refused%s" % (what, (": " + "; ".join(why)) if why else "")))
        code, warning = error_code(f)
        if code and not warning:
            raise SWError(scrub("%s: %s%s" % (what, enum_name(self.sw, "swFeatureError_e", code),
                                              (": " + "; ".join(msgs)) if msgs else "")))
        return f

    def delete(self, f):
        self.doc.ClearSelection2(True)
        if not f.Select2(False, 0) or not self.ext.DeleteSelection2(0):
            raise RuntimeError("could not delete %s" % f.Name)
        self.doc.ClearSelection2(True)

    def whats_wrong(self):
        """IModelDocExtension.GetWhatsWrong: the rebuild errors (not warnings) as 'feature: swFeatureError_e'."""
        n = g(self.ext, "GetWhatsWrongCount") or 0
        if not n:
            return []
        r = safe(lambda: self.ext.GetWhatsWrong())
        if isinstance(r, tuple) and len(r) == 4 and r[1]:
            return ["%s: %s" % (safe(lambda: r[1][i].Name, "?"), enum_name(self.sw, "swFeatureError_e", r[2][i]))
                    for i in range(len(r[1])) if not r[3][i]]
        return ["%d rebuild problems" % n]

    def save(self, path):
        """Save the document as path (in the temporary folder, outside the repository)."""
        e = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        w = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        if os.path.exists(path):
            os.remove(path)
        ok = self.ext.SaveAs(path, 0, 1, NULLDISP, e, w)            # current version, silent
        if not ok or e.value or not os.path.exists(path):
            raise RuntimeError("SaveAs %s failed (error %s)" % (os.path.basename(path), e.value))
        progress()                                                   # the document's title is now the file's
        return path


def label(kind, name):
    """How a feature is named in a result: its name when that says what it is ('Shell', 'Draft-side'), else the
    kind and the name ('Cut Groove')."""
    return name if name.lower().startswith(kind.lower()) else "%s %s" % (kind, name)


def sketch_normal(part, sk):
    """The sketch plane's normal in model coordinates."""
    xf = g(sk, "GetSpecificFeature2").ModelToSketchTransform
    inv = method(xf, "Inverse")()
    pts = [method(method(part.ses.mu, "CreatePoint")(darr(v)), "MultiplyTransform")(inv).ArrayData
           for v in ([0.0, 0.0, 0.0], [0.0, 0.0, 1.0])]
    return [pts[1][i] - pts[0][i] for i in range(3)]


def edge_dist(e, pt):
    q = list(e.GetClosestPointOn(pt[0] / 1000.0, pt[1] / 1000.0, pt[2] / 1000.0))[:3]
    return dist([c * 1000.0 for c in q], pt)


def faces_of(b):
    return list(g(b, "GetFaces") or [])


def edges_of(b):
    return list(g(b, "GetEdges") or [])


def surface_kind(f):
    return {4001: "plane", 4002: "cylinder", 4003: "cone", 4004: "sphere", 4005: "torus"}.get(
        g(g(f, "GetSurface"), "Identity"), "other")


def bbox(b):
    return [v * 1000.0 for v in g(b, "GetBodyBox")]


class Part(Doc):
    """A new part document, built feature by feature as a SOLIDWORKS user would. Lengths in mm, angles in degrees."""

    def __init__(self, ses, name):
        self._new(ses, swDefaultTemplatePart, swDocPART)
        self.name = name
        self.skm = self.doc.SketchManager

    def solids(self):
        return list(self.doc.GetBodies2(swSolidBody, False) or [])

    def placed(self):
        return [(self.doc, b, None) for b in self.solids()]

    # -- picks by point
    def face_at(self, pt, tol=TOL):
        best = nearest_face(self.solids(), pt)
        if best is None or best[0] > tol:
            raise RuntimeError("no face at %r" % (pt,))
        return best[1]

    def edges_at(self, pt, tol=TOL):
        found = [e for b in self.solids() for e in edges_of(b) if edge_dist(e, pt) < tol]
        if not found:
            raise RuntimeError("no edge at %r" % (pt,))
        return found

    def loop_at(self, face, pt, tol=TOL):
        """The edges of the face's loop that passes through pt."""
        for lp in list(g(face, "GetLoops") or []):
            es = list(g(lp, "GetEdges") or [])
            if any(edge_dist(e, pt) < tol for e in es):
                return es
        raise RuntimeError("no loop of the face at %r" % (pt,))

    def outer_loop(self, face):
        for lp in list(g(face, "GetLoops") or []):
            if g(lp, "IsOuter"):
                return list(g(lp, "GetEdges") or [])
        raise RuntimeError("the face has no outer loop")

    def body_where(self, test, what):
        hits = [b for b in self.solids() if test(bbox(b))]
        if len(hits) != 1:
            raise RuntimeError("%d bodies are %s" % (len(hits), what))
        return hits[0]

    # -- reference geometry
    def sketch(self, base, name):
        return Sketch(self, base, name)

    def plane(self, base, offset, name):
        """A plane parallel to base (a plane or a planar face) at offset (mm) along its normal."""
        def call():
            self.sel([base])
            return self.fm.InsertRefPlane(swRefPlaneDistance | (swRefPlaneFlip if offset < 0 else 0),
                                          abs(offset) / 1000.0, 0, 0.0, 0, 0.0)
        f = self.feature(call, label("Plane", name))
        f.Name = name
        return f

    def axis(self, refs, name):
        """A reference axis: where two planes meet, or along a cylindrical face."""
        def call():
            self.sel(refs)
            self.doc.InsertAxis2(True)
            return None
        f = self.feature(call, label("Axis", name))
        f.Name = name
        return f

    def angle_plane(self, base, axis, angle, name, flip=False):
        """A plane through an axis at an angle (degrees) to a plane."""
        def call():
            self.doc.ClearSelection2(True)
            if not (base.Select2(False, 0) and axis.Select2(True, 1)):
                raise RuntimeError("cannot select the plane and the axis")
            return self.fm.InsertRefPlane(swRefPlaneAngle | (swRefPlaneFlip if flip else 0), math.radians(angle),
                                          swRefPlaneCoincident, 0.0, 0, 0.0)
        f = self.feature(call, label("Plane", name))
        f.Name = name
        return f

    # -- features
    def extrude(self, sk, depth, name, direction=None, mid=False, merge=True):
        """Boss extrude of a sketch, depth mm along direction (a model vector; default the sketch normal), or
        mid-plane (total depth). Merge result joins it to the bodies it touches."""
        rev = direction is not None and sum(a * b for a, b in zip(sketch_normal(self, sk), direction)) < 0

        def call():
            self.sel([sk])
            return self.fm.FeatureExtrusion3(True, False, rev, swEndCondMidPlane if mid else swEndCondBlind,
                                             swEndCondBlind, depth / 1000.0, 0.0, False, False, False, False, 0.0,
                                             0.0, False, False, False, False, bool(merge), False, True, 0, 0.0, False)
        f = self.feature(call, label("Extrude", name))
        f.Name = name
        return f

    def cut(self, sk, name, depth=None, direction=None, both=False):
        """Extruded cut of a sketch: blind depth mm along direction (by default against the sketch normal, as
        SOLIDWORKS cuts), or through all (depth None), one way or both."""
        rev = direction is not None and sum(a * b for a, b in zip(sketch_normal(self, sk), direction)) > 0
        end = swEndCondBlind if depth is not None else swEndCondThroughAll

        def call():
            self.sel([sk])
            return self.fm.FeatureCut4(not both, False, rev, end, end, (depth or 0.0) / 1000.0, (depth or 0.0) / 1000.0,
                                       False, False, False, False, 0.0, 0.0, False, False, False, False, False, False,
                                       True, False, False, False, 0, 0.0, False, False)
        f = self.feature(call, label("Cut", name))
        f.Name = name
        return f

    def revolve(self, sk, name, merge=True):
        """Revolve a sketch 360 degrees about its centerline."""
        def call():
            self.sel([sk])
            return self.fm.FeatureRevolve2(True, True, False, False, False, False, swEndCondBlind, swEndCondBlind,
                                           2 * math.pi, 0.0, False, False, 0.0, 0.0, 0, 0.0, 0.0, bool(merge), False,
                                           True)
        f = self.feature(call, label("Revolve", name))
        f.Name = name
        return f

    def fillet(self, edges, r, name):
        """Constant-radius fillet of exactly the given edges (tangent propagation off: every edge is listed)."""
        def call():
            data = self.fm.CreateDefinition(swFmFillet)
            data.Initialize(swConstRadiusFillet)
            data.DefaultRadius = r / 1000.0
            data.PropagateToTangentFaces = False
            data.Edges = oarr(edges)
            self.doc.ClearSelection2(True)
            return self.fm.CreateFeature(data)
        f = self.feature(call, label("Fillet", name))
        f.Name = name
        return f

    def chamfer(self, edges, d, name):
        """Chamfer d x d of exactly the given edges: distance-distance (d, d), tangent propagation off. (Through
        InsertFeatureChamfer the equal-distance type makes a feature that changes nothing on 2026 SP4.1.)"""
        def call():
            self.sel(edges)
            return self.fm.InsertFeatureChamfer(0, swChamferDistanceDistance, d / 1000.0, 0.0, d / 1000.0, 0.0, 0.0,
                                                0.0)
        f = self.feature(call, label("Chamfer", name))
        f.Name = name
        return f

    def shell(self, faces, t, name, body=None):
        """Shell inward to wall t, removing the given faces; with none, the selected body is hollowed to a closed
        void (SOLIDWORKS asks for the body in a multibody part)."""
        def call():
            self.doc.ClearSelection2(True)
            self.sel(list(faces) if faces else [body], 1)
            self.doc.InsertFeatureShell(t / 1000.0, False)
            return None
        f = self.feature(call, label("Shell", name))
        f.Name = name
        return f

    def volume(self):
        return sum(body_volume(self.doc, b) for b in self.solids())

    def draft(self, side_pt, neutral_pt, angle, outward, name):
        """Neutral-plane draft of the face at side_pt (InsertMultiFaceDraft: neutral face mark 1, face to draft
        mark 2, no face propagation). SOLIDWORKS measures the angle from the pull direction, as TESTS.md does.
        The direction flag is tried both ways and the draft that adds volume (outward) or removes it (inward) is
        kept, as a user flips the direction."""
        v0 = self.volume()
        refused, other = [], None
        for flip in (False, True):
            neut, side = self.face_at(neutral_pt), self.face_at(side_pt)

            def call():
                self.sel([neut], 1)
                self.sel([side], 2, append=True)
                return self.fm.InsertMultiFaceDraft(math.radians(angle), flip, False, swFacePropNone, False, False)
            try:
                f = self.feature(call, label("Draft", name))
            except SWError as ex:
                refused.append(str(ex).split(": refused", 1)[-1].lstrip(": "))
                continue
            if (self.volume() > v0) == outward:
                f.Name = name
                return f
            other = "outward" if self.volume() > v0 else "inward"
            self.delete(f)
        why = "; ".join(r for r in refused if r)
        raise SWError("%s: the %g-degree %s draft is refused (%s)%s" % (
            label("Draft", name), angle, "outward" if outward else "inward", why or "SOLIDWORKS gives no reason",
            ("; flipped, it builds the %s one" % other) if other else ""))

    def loft(self, profiles, name, merge=True):
        """Loft through sketch profiles, each selected at the point where its connector starts ([(sketch, point)]),
        as a user picks the profiles at the corners to join. Two profiles, no end conditions: straight rulings."""
        def call():
            self.doc.ClearSelection2(True)
            for i, (sk, q) in enumerate(profiles):
                if not self.ext.SelectByID2(sk.Name, "SKETCH", q[0] / 1000.0, q[1] / 1000.0, q[2] / 1000.0, i > 0,
                                            MARK_PROFILE, NULLDISP, 0):
                    raise RuntimeError("cannot select %s at %r" % (sk.Name, q))
            return self.fm.InsertProtrusionBlend2(False, False, False, 1.0, 0, 0, 1.0, 1.0, False, False, False, 0.0,
                                                  0.0, 0, bool(merge), False, True, 0)
        f = self.feature(call, label("Loft", name))
        f.Name = name
        return f

    def helix(self, sk, pitch, revs, name, clockwise=False, reverse=False):
        """Constant-pitch helix on the sketch's circle, starting at angle 0."""
        def call():
            self.sel([sk])
            self.doc.InsertHelix(bool(reverse), bool(clockwise), False, False, swHelixDefinedByPitchAndRevolution,
                                 0.0, pitch / 1000.0, float(revs), 0.0, 0.0)
            return None
        f = self.feature(call, label("Helix", name))
        f.Name = name
        return f

    def sweep_cut(self, profile, path, name):
        """Swept cut of a sketch profile (mark 1) along a path (mark 4), following the path."""
        def call():
            self.sel([profile], MARK_PROFILE)
            self.sel([path], MARK_PATH, append=True)
            return self.fm.InsertCutSwept5(False, True, swTwistControlFollowPath, False, False, 0, 0, False, 0.0, 0.0,
                                           0, 0, False, True, 0.0, True, False, False, False, False, 0.0, 0)
        f = self.feature(call, label("Swept cut", name))
        f.Name = name
        return f

    def circular_pattern(self, seeds, axis, n, name):
        """n instances of the seed features about an axis, equally spaced over 360 degrees."""
        def call():
            self.sel([axis], MARK_AXIS)
            self.sel(seeds, MARK_SEED, append=True)
            return self.fm.FeatureCircularPattern5(int(n), 2 * math.pi, False, "NULL", False, True, False, False,
                                                   False, False, 1, 0.0, "NULL", False)
        f = self.feature(call, label("Circular pattern", name))
        f.Name = name
        return f

    def linear_pattern(self, seeds, axis, n, spacing, name, flip=False):
        """n instances of the seed features along a reference axis, spacing mm apart."""
        def call():
            self.sel([axis], MARK_AXIS)
            self.sel(seeds, MARK_SEED, append=True)
            return self.fm.FeatureLinearPattern5(int(n), spacing / 1000.0, 1, 0.0, bool(flip), False, "NULL", "NULL",
                                                 False, False, False, False, False, False, False, False, False, False,
                                                 0.0, 0.0, False, False)
        f = self.feature(call, label("Linear pattern", name))
        f.Name = name
        return f

    def split(self, tool, name):
        """Split every body by a plane and keep all the bodies (PreSplitBody2 / PostSplitBody2)."""
        def call():
            self.sel([tool])
            bodies = list(g(self.fm, "PreSplitBody2") or [])
            self.doc.ClearSelection2(True)
            if not bodies:
                return None
            n = len(bodies)
            return self.fm.PostSplitBody2(oarr(bodies), False, VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH,
                                                                       [None] * n),
                                          VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_BSTR, [""] * n), "")
        f = self.feature(call, label("Split", name))
        f.Name = name
        return f

    def move(self, bodies, t, name):
        """Move/Copy Body: move (no copy) by the translation t (mm)."""
        def call():
            self.sel(bodies, 1)
            return self.fm.InsertMoveCopyBody2(t[0] / 1000.0, t[1] / 1000.0, t[2] / 1000.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                               0.0, 0.0, False, 1)
        f = self.feature(call, label("Move", name))
        f.Name = name
        return f

    def mirror(self, bodies, plane, name, merge=False):
        """Mirror bodies (mark 256) about a plane (mark 2)."""
        def call():
            self.sel([plane], MARK_MIRROR_PLANE)
            self.sel(bodies, MARK_MIRROR_BODY, append=True)
            return self.fm.InsertMirrorFeature2(True, False, bool(merge), False, 0)
        f = self.feature(call, label("Mirror", name))
        f.Name = name
        return f

    def combine(self, op, bodies, name):
        """Combine: Add or Common of the bodies (no main body)."""
        def call():
            self.doc.ClearSelection2(True)
            return self.fm.InsertCombineFeature(op, NULLDISP, oarr(bodies))
        f = self.feature(call, label("Combine", name))
        f.Name = name
        return f

    def scale(self, factor, name):
        """Scale (uniform, about the origin)."""
        def call():
            self.doc.ClearSelection2(True)
            return self.fm.InsertScale(swScaleAboutOrigin, True, factor, factor, factor)
        f = self.feature(call, label("Scale", name))
        f.Name = name
        return f


def xform(mu, t):
    """IMathTransform for a translation t (mm): ArrayData holds the images of the axes, t (m) and a scale."""
    return method(mu, "CreateTransform")(darr([1, 0, 0, 0, 1, 0, 0, 0, 1] + [v / 1000.0 for v in t] + [1, 0, 0, 0]))


def comp_xform(comp):
    """(R, t mm) of a component: p_assembly = R p_part + t (IComponent2.Transform2)."""
    a = list(g(comp, "Transform2").ArrayData)
    if abs(a[12] - 1.0) > 1e-12:
        raise RuntimeError("a component transform with a scale")
    return [[a[3 * j + i] for j in range(3)] for i in range(3)], [a[9 + i] * 1000.0 for i in range(3)]


def z_angle(comp):
    """The component's rotation about Z (degrees): the direction of its part X axis in the assembly."""
    R, _ = comp_xform(comp)
    return math.degrees(math.atan2(R[1][0], R[0][0]))


class Assembly(Doc):
    """A new assembly document: components inserted at their places, then held by mates, as a user places them."""

    def __init__(self, ses, name):
        self._new(ses, swDefaultTemplateAssembly, swDocASSEMBLY)
        self.name = name
        self.comps = []

    def add(self, path, t):
        """Insert a part file at the translation t (mm); its document is open (this process saved it)."""
        comp = self.doc.AddComponent5(path, 0, "", False, "", t[0] / 1000.0, t[1] / 1000.0, t[2] / 1000.0)
        if comp is None:
            raise RuntimeError("AddComponent5 failed for %s" % os.path.basename(path))
        comp.Transform2 = xform(self.ses.mu, t)
        if dist(comp_xform(comp)[1], t) > 1e-9:
            raise RuntimeError("the component did not go to %r" % (t,))
        self.comps.append(comp)
        return comp

    def bodies_of(self, comp):
        """The component's solid bodies, in its part's coordinates."""
        info = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None)
        return list(comp.GetBodies3(swSolidBody, info) or [])

    def placed(self):
        out = []
        for c in self.comps:
            doc = g(c, "GetModelDoc2")
            for b in self.bodies_of(c):
                out.append((doc, b, comp_xform(c)))
        return out

    def near(self, comp, pt, kind):
        """The face or edge of a component nearest to pt (in the part's coordinates)."""
        best = None
        for b in self.bodies_of(comp):
            for e in (faces_of(b) if kind == "face" else edges_of(b)):
                d = edge_dist(e, pt)
                if best is None or d < best[0]:
                    best = (d, e)
        if best is None or best[0] > TOL:
            raise RuntimeError("no %s of %s at %r" % (kind, g(comp, "Name2"), pt))
        return best[1]

    def comp_plane(self, comp, axis):
        """The component's own principal plane normal to the part's axis 0/1/2 (x/y/z), found by its normal."""
        f = g(comp, "FirstFeature")
        while f is not None:
            if g(f, "GetTypeName2") == "RefPlane" and abs(abs(plane_normal(f)[axis]) - 1) < 1e-12:
                return f
            f = g(f, "GetNextFeature")
        raise RuntimeError("no principal plane of %s normal to axis %d" % (g(comp, "Name2"), axis))

    def mate(self, kind, a, b, name, flip=False, distance=0.0, angle=0.0, ratio=(1.0, 1.0)):
        """IAssemblyDoc.AddMate5 between two entities (faces, edges or planes of the components)."""
        def call():
            self.doc.ClearSelection2(True)
            sd = g(g(self.doc, "SelectionManager"), "CreateSelectData")
            sd.Mark = 1
            for i, e in enumerate((a, b)):
                if not (e.Select2(i > 0, 1) if is_feature(e) else e.Select4(i > 0, sd)):
                    raise RuntimeError("%s: cannot select its entity %d" % (name, i + 1))
            err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            m = self.doc.AddMate5(kind, swMateAlignCLOSEST, bool(flip), distance / 1000.0, distance / 1000.0,
                                  distance / 1000.0, ratio[0], ratio[1], math.radians(angle), math.radians(angle),
                                  math.radians(angle), False, False, 0, err)
            if err.value != swAddMateError_NoError:
                raise SWError("Mate %s: %s" % (name, enum_name(self.sw, "swAddMateError_e", err.value)))
            return m
        f = self.feature(call, label("Mate", name))
        safe(lambda: setattr(f, "Name", name))
        return f

    def rebuild(self):
        return g(self.doc, "EditRebuild3")

    def interference(self):
        """The core Interference Detection over every pair of top-level components
        (IAssemblyDoc.InterferenceDetectionManager), touching counted as interference: [(names, volume mm3)]."""
        mgr = g(self.doc, "InterferenceDetectionManager")
        mgr.TreatCoincidenceAsInterference = True
        mgr.TreatSubAssembliesAsComponents = True
        mgr.IncludeMultibodyPartInterferences = True
        mgr.MakeInterferingPartsTransparent = False
        mgr.CreateFastenersFolder = False
        mgr.IgnoreHiddenBodies = False
        mgr.ShowIgnoredInterferences = True
        mgr.UseTransform = False
        try:
            return [([g(c, "Name2") for c in list(g(itf, "Components") or [])], g(itf, "Volume") * 1e9)
                    for itf in list(g(mgr, "GetInterferences") or [])]
        finally:
            g(mgr, "Done")


# ------------------------------------------------------------------ measurement: volume, validity, probes
def body_volume(doc, body):
    """Volume (mm3) of one body by IMassProperty2 at AccuracyLevel Higher (IBody2.GetMassProperties is SOLIDWORKS'
    Lower level)."""
    mp = g(doc.Extension, "CreateMassProperty2")
    mp.UseSystemUnits = True
    mp.SelectedItems = oarr([body])
    mp.AccuracyLevel = swMassPropertyAccuracyLevel_Higher
    g(mp, "Recalculate")
    if mp.AccuracyLevel != swMassPropertyAccuracyLevel_Higher:
        raise RuntimeError("the mass property accuracy level was not accepted")
    return mp.Volume * 1e9


def body_faults(body):
    """IBody2.Check3 (Parasolid's body check): the swFaultEntityErrorCode_e codes of the faults it finds."""
    fe = g(body, "Check3")
    return [fe.ErrorCode(i) for i in range(g(fe, "Count") or 0)]


def lower_volume(body):
    """Volume (mm3) of a temporary body (IBody2.GetMassProperties), used only for the probe cube's overlap."""
    return list(body.GetMassProperties(1.0))[3] * 1e9


def body_op(a, op, b):
    """IBody2.Operations2 on two temporary bodies: (result bodies, error code)."""
    r = a.Operations2(op, b)
    if isinstance(r, tuple) and len(r) == 2 and isinstance(r[1], int):
        return list(r[0] or []), r[1]
    return list(r or []), 0


def overlap(ses, bodies, pt):
    """The share of a PROBE-sized cube centred on pt that lies inside the bodies (temporary copies of each, cut with
    SOLIDWORKS' own boolean). IModeler.CreateBodyFromBox3 takes the centre of the box's base face, an axis, and
    width, length, height."""
    a = PROBE / 1000.0
    cube = method(ses.modeler, "CreateBodyFromBox3")(darr([pt[0] / 1000.0, pt[1] / 1000.0, pt[2] / 1000.0 - a / 2,
                                                             0.0, 0.0, 1.0, a, a, a]))
    if cube is None:
        raise RuntimeError("no probe cube at %r" % (pt,))
    full = lower_volume(cube)
    v = 0.0
    for b in bodies:
        res, err = body_op(g(b, "Copy"), SWBODYINTERSECT, g(cube, "Copy"))
        if err not in (0, swBodyOperationNoIntersect) and not res:
            raise RuntimeError("the probe boolean failed at %r: %s" % (pt, enum_name(ses.sw, "swBodyOperationError_e",
                                                                                  err)))
        v += sum(lower_volume(r) for r in res)
    return v / full


def nearest_face(bodies, pt):
    """(distance mm, face, closest point mm) of the face nearest to pt (IFace2.GetClosestPointOn, which keeps to the
    bounded face)."""
    x, y, z = (c / 1000.0 for c in pt)
    best = None
    for b in bodies:
        for f in faces_of(b):
            q = [c * 1000.0 for c in list(f.GetClosestPointOn(x, y, z))[:3]]
            d = dist(q, pt)
            if best is None or d < best[0]:
                best = (d, f, q)
    return best


def outward_normal(face, q):
    """The face's outward unit normal at its point q: ISurface.EvaluateAtPoint's normal (its first three values),
    negated when IFace2.FaceInSurfaceSense is true. Measured on 2026 SP4.1 against IFace2.Normal of planar faces and
    the known outward side of cylinders, holes and tori; --selftest repeats it."""
    ev = list(g(face, "GetSurface").EvaluateAtPoint(q[0] / 1000.0, q[1] / 1000.0, q[2] / 1000.0))
    s = -1.0 if g(face, "FaceInSurfaceSense") else 1.0
    ln = math.sqrt(sum(c * c for c in ev[0:3]))
    return [s * c / ln for c in ev[0:3]]


def classify(ses, bodies, pt):
    """(is pt inside the bodies, how it was decided): see the module docstring."""
    fr = overlap(ses, bodies, pt)
    if fr <= 1e-9 or fr >= 1 - 1e-9:
        return fr >= 0.5, "cube %.6f" % fr
    d, f, q = nearest_face(bodies, pt)
    if d > 0:
        n = outward_normal(f, q)
        c = sum((p - qq) / d * nn for p, qq, nn in zip(pt, q, n))
        if abs(c) > 0.999:
            return c < 0, "nearest face, d %.3g, cos %.6f (cube %.6f)" % (d, c, fr)
    return fr >= 0.5, "cube %.6f (closest point on an edge, d %.3g)" % (fr, d)


def to_local(pt, xf):
    """A model point in a component's part coordinates: R^T (p - t)."""
    if xf is None:
        return pt
    R, t = xf
    v = [pt[i] - t[i] for i in range(3)]
    return tuple(sum(R[k][i] * v[k] for k in range(3)) for i in range(3))


def measure(state):
    pl = state.placed()
    vols = [body_volume(doc, b) for doc, b, _ in pl]
    faults = [body_faults(b) for _, b, _ in pl]
    m = {"bodies": len(pl), "volume_mm3": sum(vols), "valid": all(not f for f in faults)}
    return m, vols, faults


def inside(state, p):
    for _, b, xf in state.placed():
        ans, how = classify(SES, [b], to_local(p, xf))
        if os.environ.get("AT_DEBUG"):
            print("probe %r: %s (%s)" % (tuple(round(c, 6) for c in p), "in" if ans else "out", how), flush=True)
        if ans:
            return True
    return False


def check(stage, state, prev_volume):
    exp = values.TESTS[TEST][stage]
    m, vols, faults = measure(state)
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
        if not any(abs(s - sv) <= values.EXACT_REL * max(1.0, sv) for s in vols):
            bad.append("no solid of volume %.6f (have %s)" % (sv, ", ".join("%.6f" % s for s in vols)))
    for p in exp.get("inside", []):
        if not inside(state, p):
            bad.append("%r should be inside" % (tuple(round(c, 6) for c in p),))
    for p in exp.get("outside", []):
        if inside(state, p):
            bad.append("%r should be outside" % (tuple(round(c, 6) for c in p),))
    if not m["valid"]:
        bad.append("IBody2.Check3: %s" % ", ".join(sorted({enum_name(SES.sw, "swFaultEntityErrorCode_e", c)
                                                           for f in faults for c in f})))
    return m, bad


def emit(rec):
    rec.update(pipeline="solidworks", version=VERSION, test=TEST)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


# ------------------------------------------------------------------ the tests: each is a list of stage functions
def acid1(name="acid-1"):
    bx = math.sqrt(30 ** 2 - 0.95001 ** 2)

    def s1(_):
        p = Part(SES, name)
        # A revolved face's parameter seam (u = 0) lies on +X when the revolve axis points +Z and on -X when it
        # points -Z (measured). The centerline is drawn downward so the axis is +Z and the seam is the 0-degree
        # seam of TESTS.md, in the plane y = 0 at x > 0, next to the boss; it is checked below.
        s = p.sketch(p.top, "Tube")
        s.line((0, 0, 10), (0, 0, -10), construction=True)
        s.arc((30, 0, 0), (22, 0, 0), (38, 0, 0), (30, 0, 8))
        s.line((38, 0, 0), (22, 0, 0))
        p.revolve(s.done(), "Half-torus")
        tor = [f for f in faces_of(p.solids()[0]) if surface_kind(f) == "torus"]
        uv = list(g(tor[0], "GetUVBounds"))
        q = list(g(tor[0], "GetSurface").Evaluate(uv[0], (uv[2] + uv[3]) / 2, 0, 0))[:3]
        if len(tor) != 1 or abs(q[1]) > 1e-12 or q[0] <= 0:
            raise RuntimeError("the torus' seam is not at 0 degrees")
        return p

    def s2(p):
        s = p.sketch(p.face_at((-30, 0, 0)), "Boss")
        s.circle((bx, 0.95001, 0), 0.95)
        p.extrude(s.done(), 1.9, "Boss", direction=(0, 0, -1))
        return p

    def s3(p):
        s = p.sketch(p.face_at((-30, 0, 0)), "Notch")
        s.circle((0, 38, 0), 0.7)
        p.cut(s.done(), "Notch", depth=3, direction=(0, 0, 1))
        return p

    def s4(p):
        f = p.face_at((-30, 0, 0))
        p.fillet(p.loop_at(f, (-38, 0, 0)) + p.loop_at(f, (-22, 0, 0)), 0.8, "Round-flat")
        return p

    def s5(p):
        p.shell([p.face_at((bx, 0.95001, -1.9))], 0.6, "Shell")
        return p

    def s6(p):
        s = p.sketch(p.front, "Holes")
        for k in range(12):
            s.circle(polar(30, 15 + 30 * k, 0), 0.5)
        p.cut(s.done(), "Holes", both=True)
        return p

    def s7(p):
        p.split(p.plane(p.front, 4, "Z4"), "Split-Z4")
        return p

    def s8(p):
        p.move(p.solids(), (10000, 0, 0), "Move-10000")
        return p

    return [s1, s2, s3, s4, s5, s6, s7, s8]


def spline_outline(p, s):
    """acid-2's outline: the periodic cubic B-spline with simple knots u_0..u_8 at the chord-length parameters
    (values.SPLINE.h). Its control point j is the blossom f(u_j-2, u_j-1, u_j), read off the Bezier segments that
    values.SPLINE.bezier() gives: for segment k (b0..b3), f(u_k-1, u_k, u_k+1) = b1 - (h_k-1 / h_k)(b2 - b1).
    The sketch curve is then checked against values.SPLINE."""
    segs, h = values.SPLINE.bezier(), values.SPLINE.h
    n = len(segs)
    knots = [0.0]
    for k in range(n):
        knots.append(knots[-1] + h[k])
    blossom = [[segs[k][1][i] - h[k - 1] / h[k] * (segs[k][2][i] - segs[k][1][i]) for i in range(2)] for k in range(n)]
    poles = [blossom[(j - 1) % n] for j in range(n)]
    seg = s.bspline([(x, y, 0.0) for x, y in poles], knots, True)
    if len(seg) != 1:
        raise RuntimeError("the outline came out as %d sketch curves" % len(seg))
    c = g(seg[0], "GetCurve")
    worst = 0.0
    for k in range(n):
        for t in (0.0, 0.2, 0.5, 0.8):
            x, y = values.SPLINE.at(k, t)
            worst = max(worst, edge_dist(c, (x, y, 0.0)))
    if worst > 1e-9:
        raise RuntimeError("the outline is %.3g mm from values.SPLINE" % worst)
    return seg


def acid2(name="acid-2"):
    def s1(_):
        p = Part(SES, name)
        s = p.sketch(p.front, "Outline")
        spline_outline(p, s)
        p.extrude(s.done(), 25, "Outline", direction=(0, 0, 1))
        return p

    def s2(p):
        p.fillet(p.loop_at(p.face_at((0, 0, 25)), (60, 0, 25), tol=1e-2), 3, "Round-top")
        return p

    def s3(p):
        s = p.sketch(p.face_at((0, 0, 25)), "Bosses")
        for x in (-30, 30):
            for y in (-12, 12):
                s.circle((x, y, 25), 3)
        p.extrude(s.done(), 10, "Bosses", direction=(0, 0, 1))
        return p

    def s4(p):
        p.draft((60, 0, 10), (0, 0, 0), 3, True, "Draft-side")
        return p

    def s5(p):
        p.shell([p.face_at((0, 0, 0))], 1.5, "Shell")
        return p

    def s6(p):
        s = p.sketch(p.front, "Holes")
        for x in (-30, 30):
            for y in (-12, 12):
                s.circle((x, y, 0), 2)
        p.cut(s.done(), "Holes", direction=(0, 0, 1))
        return p

    def s7(p):
        p.mirror(p.solids(), p.plane(p.right, 80, "X80"), "Mirror-X80")
        return p

    def s8(p):
        s = p.sketch(p.front, "Bar")
        s.rect((55, -5), (105, 5), 0)
        p.extrude(s.done(), 5, "Bar", direction=(0, 0, 1))
        return p

    return [s1, s2, s3, s4, s5, s6, s7, s8]


def acid3():
    r_minor = 4.917 / 2

    def s1(_):
        p = Part(SES, "acid-3")
        s = p.sketch(p.front, "Block")
        s.rect((0, 0), (80, 60), 0)
        p.extrude(s.done(), 30, "Block", direction=(0, 0, 1))
        return p

    def s2(p):
        s = p.sketch(p.face_at((40, 30, 30)), "Pads")
        for i in range(3):
            for j in range(2):
                cx, cy = 20 + 20 * i, 20 + 20 * j
                s.rect((cx - 1.5, cy - 1.5), (cx + 1.5, cy + 1.5), 30)
        p.extrude(s.done(), 2, "Pads", direction=(0, 0, 1))
        return p

    def s3(p):
        s = p.sketch(p.face_at((30, 30, 30)), "Hole")
        s.circle((40, 30, 30), r_minor)
        p.cut(s.done(), "Hole", depth=20, direction=(0, 0, -1))
        # the thread: a helix of pitch 1 on the minor circle from z = 15 to 30, and a swept cut of the 60-degree V
        # profile freecad/acid.py uses, drawn in the plane y = 30 at the helix's start
        s = p.sketch(p.plane(p.front, 15, "Z15"), "Helix-circle")
        s.circle((40, 30, 15), r_minor)
        hc = s.done()
        hx = p.helix(hc, 1.0, 15, "Thread-helix")              # not clockwise: right-handed, rising +Z (checked)
        start, right_hand = helix_start(hx, (40, 30, 15))
        if dist(start, (40 + r_minor, 30, 15)) > 1e-6 or not right_hand:
            raise RuntimeError("the helix starts at %r (right hand %s)" % (start, right_hand))
        depth = 3.0 - (r_minor - 0.02)
        hw = depth * math.tan(math.radians(30))
        s = p.sketch(p.plane(p.top, 30, "Y30"), "Thread-profile")
        s.polygon([(40 + r_minor - 0.02, 30, 15 - hw), (43.0, 30, 15), (40 + r_minor - 0.02, 30, 15 + hw)])
        p.sweep_cut(s.done(), hx, "Thread")
        return p

    def s4(p):
        s = p.sketch(p.plane(p.front, 32, "Z32"), "Pin-hole")
        s.circle((52, 30, 32), 1.25)
        seed = p.cut(s.done(), "Pin-hole", depth=12, direction=(0, 0, -1))
        ax = p.axis([p.face_at((40 + r_minor, 30, 12))], "Hole-axis")
        p.circular_pattern([seed], ax, 8, "Pin-holes")
        return p

    def s5(p):
        s = p.sketch(p.face_at((10, 10, 30)), "Notch")
        s.circle((1.50001, 0, 30), 1.5)
        p.cut(s.done(), "Notch", depth=4, direction=(0, 0, -1))
        return p

    def s6(p):
        p.shell([], 1.2, "Shell", body=p.solids()[0])
        return p

    def s7(p):
        p.fillet(p.outer_loop(p.face_at((40, 0.5, 30))), 0.5, "Round-top")
        return p

    return [s1, s2, s3, s4, s5, s6, s7]


def helix_start(hx, near):
    """(the helix end nearest to near, whether it winds right-handed: counterclockwise seen from +Z as it rises)."""
    best = None
    for e in list(g(g(hx, "GetSpecificFeature2"), "GetSegments") or []):
        c = g(e, "GetCurve")
        cp = g(e, "GetCurveParams3")
        for t, sgn in ((cp.UMinValue, 1.0), (cp.UMaxValue, -1.0)):
            ev = list(c.Evaluate2(t, 1))
            P = [v * 1000.0 for v in ev[:3]]
            d = dist(P, near)
            if best is None or d < best[0]:
                tan = [sgn * v for v in ev[3:6]]
                rad = [P[0] - near[0], P[1] - near[1]]
                best = (d, P, tan[2] > 0 and rad[0] * tan[1] - rad[1] * tan[0] > 0)
    if best is None:
        raise RuntimeError("the helix has no curve")
    return best[1], best[2]


def acid4():
    mids = [(x, y, 0) for x in (-20, 20) for y in (-20, 20)] + [(x, 0, z) for x in (-20, 20) for z in (-20, 20)] \
        + [(0, y, z) for y in (-20, 20) for z in (-20, 20)]

    def s1(_):
        p = Part(SES, "acid-4")
        s = p.sketch(p.front, "Cube")
        s.rect((-20, -20), (20, 20), 0)
        p.extrude(s.done(), 40, "Cube", mid=True)
        return p

    def s2(p):
        for plane, c, nm in ((p.right, (0, 0, 0), "X"), (p.top, (0, 0, 0), "Y"), (p.front, (0, 0, 0), "Z")):
            s = p.sketch(plane, "Hole-" + nm)
            s.circle(c, 9)
            p.cut(s.done(), "Hole-" + nm, both=True)
        return p

    def s3(p):
        s = p.sketch(p.right, "Holes-X")
        for y in (-10, 10):
            s.circle((0, y, 0), 4)
        p.cut(s.done(), "Holes-X", both=True)
        s = p.sketch(p.top, "Holes-Y")
        for x in (-10, 10):
            s.circle((x, 0, 1e-5), 4)
        p.cut(s.done(), "Holes-Y", both=True)
        return p

    def s4(p):
        s = p.sketch(p.front, "Cylinder-1")
        s.circle((26, 0, 0), 5)
        p.extrude(s.done(), 60, "Cylinder-1", mid=True, merge=False)
        s = p.sketch(p.front, "Cylinder-2")
        s.circle((35.99999, 0, 0), 5)
        p.extrude(s.done(), 60, "Cylinder-2", mid=True, merge=True)
        return p

    def s5(p):
        s = p.sketch(p.top, "Sphere")
        s.line((26, 0, 28), (26, 0, 42), construction=True)
        s.arc((26, 0, 34.99999), (26, 0, 29.99999), (26, 0, 39.99999), (31, 0, 34.99999))
        s.line((26, 0, 39.99999), (26, 0, 29.99999))
        p.revolve(s.done(), "Sphere", merge=True)
        return p

    def s6(p):
        cube = p.body_where(lambda bb: bb[3] < 20.5, "the cube")
        p.fillet([[e for e in edges_of(cube) if edge_dist(e, q) < TOL][0] for q in mids], 2, "Round-cube")
        return p

    return [s1, s2, s3, s4, s5, s6]


def acid5():
    def s1(_):
        p = Part(SES, "acid-5")
        s = p.sketch(p.plane(p.front, 1000, "Z1000"), "Block")
        s.rect((1000, 1000), (1030, 1020), 1000)
        p.extrude(s.done(), 10, "Block", direction=(0, 0, 1))
        return p

    def s2(p):
        p.fillet([p.edges_at((x, y, 1005))[0] for x in (1000, 1030) for y in (1000, 1020)], 1.3, "Round-vertical")
        return p

    def s3(p):
        p.fillet(p.loop_at(p.face_at((1015, 1010, 1010)), (1015, 1000, 1010)), 1.29, "Round-top")
        return p

    def s4(p):
        p.chamfer(p.edges_at((1015, 1000, 1000)), 0.00002, "Chamfer-x")
        return p

    def s5(p):
        p.fillet(p.edges_at((1000, 1010, 1000)), 0.000025, "Round-y")
        return p

    def s6(p):
        p.shell([p.face_at((1015, 1010, 1000))], 0.4, "Shell")
        return p

    return [s1, s2, s3, s4, s5, s6]


def acid6():
    z0 = 499.99975

    def s1(_):
        p = Part(SES, "acid-6")
        s = p.sketch(p.top, "Sphere")
        s.line((0, 0, -510), (0, 0, 510), construction=True)
        s.arc((0, 0, 0), (0, 0, -500), (0, 0, 500), (500, 0, 0))
        s.line((0, 0, 500), (0, 0, -500))
        p.revolve(s.done(), "Sphere")
        s = p.sketch(p.plane(p.front, z0, "Z-flat"), "Flat")
        s.rect((-600, -600), (600, 600), z0)
        p.cut(s.done(), "Flat", direction=(0, 0, 1))
        return p

    def s2(p):
        s = p.sketch(p.face_at((0, 0, z0)), "Pin")
        s.circle((0, 0, z0), 0.06)
        p.extrude(s.done(), 0.12, "Pin", direction=(0, 0, 1))
        return p

    def s3(p):
        s = p.sketch(p.top, "Ring")
        s.line((0, 0, -10), (0, 0, 10), construction=True)
        s.arc((500, 0, 0), (499.5, 0, 0), (500.5, 0, 0), (500, 0, 0.5))
        s.line((500.5, 0, 0), (499.5, 0, 0))
        p.revolve(s.done(), "Ring", merge=False)
        p.move([p.body_where(lambda bb: bb[5] < 1, "the ring")], (0, 0, 600), "Ring-up")
        return p

    def s4(p):
        p.shell([], 0.06, "Hollow-ring", body=p.body_where(lambda bb: bb[2] > 590, "the ring"))
        return p

    def s5(p):
        s = p.sketch(p.plane(p.front, -1600, "Z-1600"), "Needle")
        s.circle((0, 0, -1600), 0.5)
        p.extrude(s.done(), 1000, "Needle", direction=(0, 0, 1), merge=False)
        p.draft((0.5, 0, -1100), (0, 0, -1600), 0.02, False, "Draft-needle")
        return p

    def s6(p):
        s = p.sketch(p.plane(p.front, -600, "Z-600"), "Rod")
        s.circle((0, 0, -600), 0.4)
        p.extrude(s.done(), 200, "Rod", direction=(0, 0, 1), merge=True)
        return p

    return [s1, s2, s3, s4, s5, s6]


def acid7():
    def s1(_):
        p = Part(SES, "acid-7")
        s = p.sketch(p.top, "Cone")
        s.line((0, 0, -10), (0, 0, 210), construction=True)
        s.polygon([(0, 0, 0), (100, 0, 0), (40, 0, 200), (0, 0, 200)])
        p.revolve(s.done(), "Cone")
        return p

    def s2(p):
        s = p.sketch(p.face_at((0, 0, 200)), "Holes")
        for k in range(6):
            s.circle(polar(32, 60 * k, 200), 5)
        p.cut(s.done(), "Holes", depth=50, direction=(0, 0, -1))
        return p

    def s3(p):
        p.draft((70, 0, 100), (0, 0, 0), 18, False, "Draft-side")
        return p

    def s4(p):
        p.fillet(p.outer_loop(p.face_at((0, 0, 200))), 0.5, "Round-top")
        return p

    def s5(p):
        s = p.sketch(p.front, "Pocket")
        s.circle((0, 0, 0), 60)
        p.cut(s.done(), "Pocket", depth=80, direction=(0, 0, 1))
        return p

    def s6(p):
        p.shell([p.face_at((80, 0, 0))], 2, "Shell")
        return p

    return [s1, s2, s3, s4, s5, s6]


def acid8():
    st = {}

    def s1(_):
        p = Part(SES, "acid-8")
        s = p.sketch(p.front, "Drum")
        s.circle((0, 0, 0), 50)
        p.extrude(s.done(), 120, "Drum", direction=(0, 0, 1))
        return p

    def s2(p):
        s = p.sketch(p.plane(p.right, 50, "X50"), "Hole")
        s.circle((50, 0, 20), 3)
        st["seed"] = p.cut(s.done(), "Hole", depth=20, direction=(-1, 0, 0))
        st["axis"] = p.axis([p.top, p.right], "Z-axis")
        st["ring"] = p.circular_pattern([st["seed"]], st["axis"], 24, "Ring")
        return p

    def s3(p):
        # the pattern runs from the axis' start point to its end point (IRefAxis.GetRefAxisParams) unless flipped
        a = list(g(g(st["axis"], "GetSpecificFeature2"), "GetRefAxisParams"))
        p.linear_pattern([st["seed"], st["ring"]], st["axis"], 5, 20, "Rings", flip=a[5] < a[2])
        return p

    def s4(p):
        s = p.sketch(p.face_at((0, 0, 120)), "Bosses")
        for k in range(7):
            s.circle(polar(30, 15 * k, 120), 2)
        p.extrude(s.done(), 3, "Bosses", direction=(0, 0, 1))
        return p

    def s5(p):
        p.shell([], 2, "Shell", body=p.solids()[0])
        return p

    def s6(p):
        p.split(p.plane(p.front, 61, "Z61"), "Split-Z61")
        return p

    def s7(p):
        upper = max(p.solids(), key=lambda b: bbox(b)[5])
        p.mirror([upper], p.plane(p.front, 140, "Z140"), "Mirror-Z140")
        return p

    return [s1, s2, s3, s4, s5, s6, s7]


def tower_edge_points():
    """Points on the edges where the sphere (r 55 at (0, 0, 50)) meets the four twisted faces: on each face, near
    both ends of its bottom and top edges, found by bisection along the ruling (as solidcraft/acid.py)."""
    def P(i, u, t):
        a0, a1 = values.tower_section(0)[i], values.tower_section(0)[(i + 1) % 4]
        b0, b1 = values.tower_section(100)[i], values.tower_section(100)[(i + 1) % 4]
        A = [a0[k] + u * (a1[k] - a0[k]) for k in range(2)]
        B = [b0[k] + u * (b1[k] - b0[k]) for k in range(2)]
        return [A[0] + t * (B[0] - A[0]), A[1] + t * (B[1] - A[1]), 100 * t]

    def d(p):
        return math.sqrt(p[0] ** 2 + p[1] ** 2 + (p[2] - 50) ** 2) - 55

    pts = []
    for i in range(4):
        for u in (0.02, 0.98):
            for lo, hi in ((0.0, 0.5), (1.0, 0.5)):
                if d(P(i, u, lo)) <= 0:
                    continue
                for _ in range(80):
                    mid = (lo + hi) / 2
                    lo, hi = (mid, hi) if d(P(i, u, mid)) > 0 else (lo, mid)
                pts.append(P(i, u, (lo + hi) / 2))
    return pts


def acid9():
    def s1(_):
        p = Part(SES, "acid-9")
        c0, c1 = values.tower_section(0), values.tower_section(100)
        s = p.sketch(p.front, "Square-0")
        s.polygon([(x, y, 0.0) for x, y in c0])
        a = s.done()
        s = p.sketch(p.plane(p.front, 100, "Z100"), "Square-100")
        s.polygon([(x, y, 100.0) for x, y in c1])
        b = s.done()
        # each profile picked at the corners to join: (20, 20, 0) goes to the corner 45 degrees further around
        p.loft([(a, (c0[0][0], c0[0][1], 0.0)), (b, (c1[0][0], c1[0][1], 100.0))], "Tower")
        return p

    def s2(p):
        s = p.sketch(p.front, "Bore")
        s.circle((0, 0, 0), 36.94 / 2)
        p.cut(s.done(), "Bore", direction=(0, 0, 1))
        return p

    def s3(p):
        s = p.sketch(p.top, "Sphere")
        s.line((0, 0, -10), (0, 0, 110), construction=True)
        s.arc((0, 0, 50), (0, 0, -5), (0, 0, 105), (55, 0, 50))
        s.line((0, 0, 105), (0, 0, -5))
        p.revolve(s.done(), "Sphere", merge=False)
        p.combine(SWBODYINTERSECT, p.solids(), "Common")
        return p

    def s4(p):
        edges = []
        for q in tower_edge_points():
            hit = [e for e in p.edges_at(q) if e not in edges]
            edges += hit[:1]
        kinds = lambda e: sorted(surface_kind(f) for f in list(g(e, "GetTwoAdjacentFaces2") or []) if f is not None)
        wanted = [e for b in p.solids() for e in edges_of(b) if kinds(e) == ["other", "sphere"]]
        if len(edges) != len(wanted) or any(kinds(e) != ["other", "sphere"] for e in edges):
            raise RuntimeError("picked %d edges, %d are sphere-to-twisted-face edges" % (len(edges), len(wanted)))
        p.fillet(edges, 1, "Round-sphere")
        return p

    def s5(p):
        p.shell([p.face_at((0, 22, 100))], 1, "Shell")
        return p

    def s6(p):
        ax = p.axis([p.top, p.right], "Z-axis")
        pl = p.angle_plane(p.right, ax, 45, "Diagonal")
        n = plane_normal(pl)
        if abs(abs(n[0] - n[1]) / math.sqrt(2) - 1) > 1e-9:        # the normal must be +-(1, -1, 0) / sqrt 2
            p.delete(pl)
            pl = p.angle_plane(p.right, ax, 45, "Diagonal", flip=True)
            n = plane_normal(pl)
            if abs(abs(n[0] - n[1]) / math.sqrt(2) - 1) > 1e-9:
                raise RuntimeError("the diagonal plane has the normal %r" % (n,))
        p.split(pl, "Split-diagonal")
        return p

    return [s1, s2, s3, s4, s5, s6]


def acid10():
    def s1(_):
        p = Part(SES, "acid-10")
        s = p.sketch(p.front, "Block")
        s.rect((0, 0), (50, 50), 0)
        p.extrude(s.done(), 50, "Block", direction=(0, 0, 1))
        return p

    def s2(p):
        s = p.sketch(p.right, "Cylinder")
        s.circle((0, 25, 59.99998), 10)
        p.extrude(s.done(), 50, "Cylinder", direction=(1, 0, 0))
        return p

    def s3(p):
        s = p.sketch(p.plane(p.front, 25, "Z25"), "Sphere")
        s.line((48, 25, 25), (82, 25, 25), construction=True)
        s.arc((64.99998, 25, 25), (49.99998, 25, 25), (79.99998, 25, 25), (64.99998, 40, 25))
        s.line((79.99998, 25, 25), (49.99998, 25, 25))
        p.revolve(s.done(), "Sphere")
        return p

    def s4(p):
        s = p.sketch(p.plane(p.front, -1, "Z-1"), "Groove")
        s.rect((20, -1), (30, 0.00002), -1)
        p.cut(s.done(), "Groove", depth=52, direction=(0, 0, 1))
        return p

    def s5(p):
        p.fillet([p.edges_at((x, y, 25))[0] for x in (0, 50) for y in (0, 50)], 5, "Round-vertical")
        return p

    def s6(p):
        p.move(p.solids(), (1000000, 1000000, 0), "Move-1km")
        return p

    return [s1, s2, s3, s4, s5, s6]


def acid11():
    """The orrery: base, ring and cam parts in an assembly. Joints are concentric + distance mates, the gear is a
    gear mate, and the ring is driven through an angle mate whose value is stepped through the API."""
    st = {}
    steps = values.TESTS["acid-11"][5]["motion"]["steps"]

    def build(fn, upto):
        state = None
        for f in fn()[:upto]:
            state = f(state)
        return state

    def s1(_):
        os.makedirs(TMP, exist_ok=True)
        p = Part(SES, "acid-11-base")
        s = p.sketch(p.front, "Plate")
        s.rect((-80, -50), (80, 50), 0)
        p.extrude(s.done(), 10, "Plate", direction=(0, 0, 1))
        s = p.sketch(p.face_at((0, 30, 10)), "Pins")
        s.circle((-40, 0, 10), 3)
        s.circle((13.5, 0, 10), 3)
        p.extrude(s.done(), 2, "Pins", direction=(0, 0, 1))
        path = p.save(os.path.join(TMP, "acid-11-base.SLDPRT"))
        a = Assembly(SES, "acid-11")
        st["base"] = a.add(path, (0, 0, 0))
        if not g(st["base"], "IsFixed"):
            raise RuntimeError("the base is not fixed")
        return a

    def s2(a):
        ring = build(lambda: acid1("acid-11-ring"), 3)
        path = ring.save(os.path.join(TMP, "acid-11-ring.SLDPRT"))
        c = st["ring"] = a.add(path, (-40, 0, 30))
        a.mate(swMateCONCENTRIC, a.near(c, (-22, 0, 0), "edge"), a.near(st["base"], (-37, 0, 11), "face"), "Ring-axis")
        a.mate(swMateDISTANCE, a.near(c, (-30, 0, 0), "face"), a.near(st["base"], (0, 30, 10), "face"), "Ring-height",
               distance=20)
        a.rebuild()
        return a

    def s3(a):
        cam = build(lambda: acid2("acid-11-cam"), 3)
        cam.scale(0.25, "Scale")
        s = cam.sketch(cam.front, "Axle")
        s.circle((0, 0, 0), 1)
        cam.cut(s.done(), "Axle", both=True)
        path = cam.save(os.path.join(TMP, "acid-11-cam.SLDPRT"))
        c = st["cam"] = a.add(path, (13.5, 0, 30))
        a.mate(swMateCONCENTRIC, a.near(c, (1, 0, 3), "face"), a.near(st["base"], (16.5, 0, 11), "face"), "Cam-axis")
        a.mate(swMateDISTANCE, a.near(c, (4, 4, 0), "face"), a.near(st["base"], (0, 30, 10), "face"), "Cam-height",
               distance=20)
        a.rebuild()
        return a

    def s4(a):
        a.mate(swMateGEAR, a.near(st["ring"], (-22, 0, 0), "edge"), a.near(st["cam"], (1, 0, 3), "face"), "Gear",
               ratio=(3.0, 1.0))
        a.rebuild()
        return a

    def drive(a, angle):
        """Set the driving angle mate (made at the first step) to angle, and rebuild."""
        if "drive" not in st:
            m = a.mate(swMateANGLE, a.comp_plane(st["ring"], 0), a.right, "Drive", angle=0.0)
            st["drive"] = g(g(g(m, "GetSpecificFeature2"), "DisplayDimension"), "GetDimension2", 0)
        # the angle mate turns the ring by minus its value about +Z (measured; every step's ring angle is checked):
        # the value is set to 360 - angle so the ring turns +angle
        st["drive"].SetSystemValue3(math.radians((360 - angle) % 360), swSetValue_InAllConfigurations, None)
        if not a.rebuild():
            raise SWError("the assembly did not rebuild at %g degrees: %s" % (angle, "; ".join(a.whats_wrong())))
        return z_angle(st["ring"]), z_angle(st["cam"])

    def s5(a):
        exp = values.TESTS[TEST][5]["motion"]
        bad, frames = [], []
        for k, want in enumerate(exp["steps"]):
            r, c = drive(a, want)
            frames.append((k, r, c))
        for k, r, c in frames:
            want = exp["steps"][k]
            dr = abs(((r - want + 180) % 360) - 180)
            dc = abs(((c - exp["ratio"] * r + 180) % 360) - 180)
            if dr > exp["tol_deg"] or dc > exp["tol_deg"]:
                bad.append("step %d: ring %.9f (want %g), cam %.9f (want %.9f)" % (k, r, want, c, (exp["ratio"] * r) % 360))
                break
        print("acid-11 motion: %d steps, worst ring error %.3g deg, worst cam error %.3g deg" % (
            len(frames), max(abs(((r - exp["steps"][k] + 180) % 360) - 180) for k, r, _ in frames),
            max(abs(((c - exp["ratio"] * r + 180) % 360) - 180) for _, r, c in frames)), flush=True)
        return a, bad

    def s6(a):
        bad = []
        for k, want in enumerate(steps):
            r, c = drive(a, want)
            if abs(((r - want + 180) % 360) - 180) > 1e-6:
                bad.append("step %d: the ring is at %.9f, not %g" % (k, r, want))
                break
            for names, v in a.interference():
                bad.append("step %d: %s interfere (%.3g mm3)" % (k, " and ".join(n.split("-")[-2] if "-" in n else n
                                                                                 for n in names), v))
            if bad:
                break
        print("acid-11 interference: %d steps checked, %d interferences" % (k + 1, len(bad)), flush=True)
        return a, bad

    return [s1, s2, s3, s4, s5, s6]


BUILDERS = {"acid-1": acid1, "acid-2": acid2, "acid-3": acid3, "acid-4": acid4, "acid-5": acid5,
            "acid-6": acid6, "acid-7": acid7, "acid-8": acid8, "acid-9": acid9, "acid-10": acid10,
            "acid-11": acid11}


# ------------------------------------------------------------------ the probe's and the interference check's own test
def selftest():
    """Points 0.000005 mm inside and outside the faces of a block, a hole, a cylinder, a sphere and a torus, near
    the origin and up to 100 m away; and an interference the check must find. Prints each answer; returns the
    number wrong."""
    E = 0.000005
    wrong = []

    def chk(bodies, pt, want, label):
        ans, how = classify(SES, bodies, pt)
        print("%-30s %-44s %-3s %s  %s" % (label, tuple(round(c, 7) for c in pt), "in" if ans else "out",
                                          "ok" if ans == want else "WRONG", how), flush=True)
        if ans != want:
            wrong.append(label)

    p = Part(SES, "selftest")
    s = p.sketch(p.front, "Block")
    s.rect((0, 0), (10, 10), 0)
    s.circle((5, 5, 0), 2)
    p.extrude(s.done(), 10, "Block", direction=(0, 0, 1))
    blk = p.solids()
    for pt, want, lab in [((5, 8, 10 - E), True, "block top -e"), ((5, 8, 10 + E), False, "block top +e"),
                          ((10 - E, 5, 5), True, "block +x -e"), ((10 + E, 5, 5), False, "block +x +e"),
                          ((5, E, 5), True, "block -y +e"), ((5, -E, 5), False, "block -y -e"),
                          ((10 - E, 10 - E, 5), True, "block edge -e -e"), ((10 + E, 10 + E, 5), False, "block edge +e +e"),
                          ((5, 3 - E, 5), True, "hole wall -e"), ((5, 3 + E, 5), False, "hole wall +e"),
                          ((1, 1, 1), True, "block deep"), ((5, 5, 5), False, "hole centre")]:
        chk(blk, pt, want, lab)
    for off in (0.0, 1000.0, 100000.0):
        pl = p.plane(p.front, off, "P%g" % off) if off else p.front
        s = p.sketch(pl, "Cylinder%g" % off)
        s.circle((off + 30, off, off), 5)
        p.extrude(s.done(), 10, "Cylinder%g" % off, direction=(0, 0, 1), merge=False)
        cyl = [b for b in p.solids() if abs(bbox(b)[0] - (off + 25)) < 1e-6]
        for ang in (0, 45, 77, 251):
            c, sn = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            chk(cyl, (off + 30 + (5 - E) * c, off + (5 - E) * sn, off + 5), True, "cylinder at %g r-e %d" % (off, ang))
            chk(cyl, (off + 30 + (5 + E) * c, off + (5 + E) * sn, off + 5), False, "cylinder at %g r+e %d" % (off, ang))
    s = p.sketch(p.top, "Sphere")
    s.line((0, 0, 1490), (0, 0, 2510), construction=True)
    s.arc((0, 0, 2000), (0, 0, 1500), (0, 0, 2500), (500, 0, 2000))
    s.line((0, 0, 2500), (0, 0, 1500))
    p.revolve(s.done(), "Sphere", merge=False)
    sph = [b for b in p.solids() if bbox(b)[2] > 1000]
    for d in ((0, 0, 1), (1, 1, 1), (0.3, -0.8, 0.52), (-1, 0.2, -0.1)):
        u = [v / math.sqrt(sum(w * w for w in d)) for v in d]
        for sgn, want in ((-1, True), (1, False)):
            r = 500 + sgn * E
            chk(sph, (r * u[0], r * u[1], 2000 + r * u[2]), want, "sphere r%se %s" % ("+" if sgn > 0 else "-", d))
    s = p.sketch(p.top, "Torus")
    s.line((0, 0, -120), (0, 0, -80), construction=True)
    s.arc((30, 0, -100), (22, 0, -100), (38, 0, -100), (30, 0, -92))
    s.line((38, 0, -100), (22, 0, -100))
    p.revolve(s.done(), "Torus", merge=False)
    tor = [b for b in p.solids() if bbox(b)[5] < -50]
    for ang in (0.0, 0.5, 200.0):
        c, sn = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        rr, zz = 30 + 8 * math.cos(0.7), -100 + 8 * math.sin(0.7)
        for sgn, want in ((-1, True), (1, False)):
            r2, z2 = rr + sgn * E * math.cos(0.7), zz + sgn * E * math.sin(0.7)
            chk(tor, (r2 * c, r2 * sn, z2), want, "torus side %se at %g" % ("+" if sgn > 0 else "-", ang))
            chk(tor, (30 * c, 30 * sn, -92 + sgn * E), want, "torus top %se at %g" % ("+" if sgn > 0 else "-", ang))
            chk(tor, (30 * c, 30 * sn, -100 - sgn * E), want, "torus flat %se at %g" % ("-" if sgn > 0 else "+", ang))
    # the interference check finds two overlapping parts, and nothing once they are apart
    os.makedirs(TMP, exist_ok=True)
    path = p.save(os.path.join(TMP, "selftest.SLDPRT"))
    a = Assembly(SES, "selftest")
    a.add(path, (0, 0, 0))
    c2 = a.add(path, (0, 0, 9))
    found = a.interference()
    print("interference, overlapping copies: %s" % found, flush=True)
    if not found:
        wrong.append("interference not found")
    c2.Transform2 = xform(SES.mu, (0, 0, 3000))
    found = a.interference()
    print("interference, copies apart: %s" % found, flush=True)
    if found:
        wrong.append("interference where there is none")
    return wrong


def main():
    global SES, VERSION
    progress(1)
    SES = Session()
    VERSION = sw_version(SES.sw)
    if "--version" in sys.argv:
        print(VERSION)
        return 0
    cip = bool(SES.sw.CommandInProgress)
    SES.sw.CommandInProgress = True           # a session flag (not a preference): faster calls; restored below
    try:
        if "--selftest" in sys.argv:
            wrong = selftest()
            print("selftest: %d wrong %s" % (len(wrong), wrong), flush=True)
            return 1 if wrong else 0
        stages = BUILDERS[TEST]()
        state, prev_v, passed = None, None, 0
        for n in range(1, len(values.TESTS[TEST]) + 1):
            progress(n)
            t0 = time.time()
            extra = []
            try:
                state = stages[n - 1](state)
                if isinstance(state, tuple):
                    state, extra = state
                ww = state.whats_wrong()
                if ww:
                    raise SWError("rebuild errors: %s" % "; ".join(ww))
            except SWError as e:
                emit({"stage": n, "outcome": "error", "seconds": round(time.time() - t0, 3),
                      "detail": scrub(str(e))[:300]})
                break
            except Exception as e:  # a pick that finds nothing, or a call SOLIDWORKS answers with nothing
                traceback.print_exc()
                emit({"stage": n, "outcome": "error", "seconds": round(time.time() - t0, 3),
                      "detail": scrub("%s: %s" % (type(e).__name__, str(e).strip()))[:300]})
                break
            secs = round(time.time() - t0, 3)
            try:
                m, bad = check(n, state, prev_v)
            except Exception as e:
                traceback.print_exc()
                emit({"stage": n, "outcome": "fail", "seconds": secs,
                      "detail": scrub("the checks could not be made: %s: %s" % (type(e).__name__, e))[:300]})
                prev_v = None
                continue
            bad += extra
            emit({"stage": n, "outcome": "pass" if not bad else "fail", "seconds": secs, "measured": m,
                  "detail": scrub("; ".join(bad))})
            print("%s %d %s %.3f s %s" % (TEST, n, "pass" if not bad else "fail", secs, "; ".join(bad)[:200]),
                  flush=True)
            prev_v = m["volume_mm3"]
            passed += not bad
        if passed == len(values.TESTS[TEST]) and os.environ.get("AT_MESH"):
            # the finished output for the site (tools/run_solidworks.py renders it); SOLIDWORKS writes an STL when
            # the name ends in .stl, and may write an assembly as one file per component, named after this one
            try:
                os.makedirs(os.path.dirname(os.environ["AT_MESH"]), exist_ok=True)
                state.save(os.environ["AT_MESH"])
            except Exception as e:
                print("the finished output was not exported: %s" % e, flush=True)
    finally:
        SES.close_all()
        SES.sw.CommandInProgress = cip
    return 0


if __name__ == "__main__":
    sys.exit(main())
