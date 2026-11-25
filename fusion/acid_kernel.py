"""Run neutral kernel ops (expected/kernel_ops.py) on Fusion's kernel through its transient B-rep API.

This file runs inside Fusion: tools/run_fusion_kernel.py sends it through Fusion's local MCP server (the
"script" execution) with STAGES (the op lists of stages 1..n), PROBES and TIMED (the stage to time)
appended. It uses only adsk.fusion.TemporaryBRepManager and BRepBody queries: no document, no
component, no timeline feature, no parameter, no sketch. Bodies live only in memory for the script's run;
nothing is added to a design or saved.

Each op maps to one TemporaryBRepManager call (docs/kernel-ops.md). Fusion's API works in cm; the ops
are in mm. Stages 1..n-1 are rebuilt untimed, then stage n is built and timed, then measured:
  bodies  the number of lumps summed over the bodies
  valid   BRepBody.isSolid for every body (TemporaryBRepManager has no further validity check)
  volume  getPhysicalProperties(VeryHighCalculationAccuracy) of a copy of each body placed in a scratch
          direct-modelling design (no timeline, no features), which is closed unsaved straight after:
          a temporary body's getPhysicalProperties is 0 at every accuracy, and BRepBody.volume is
          Fusion's coarse estimate (1e-5 relative off on acid-4 and acid-8, beyond the checks' slack);
          BRepBody.volume is reported too, as tbm_vols
  probes  BRepBody.pointContainment at each point: inside, outside or on (Fusion's tolerance of a face)
"""
import json
import math
import time

import adsk.core
import adsk.fusion

MM = 0.1


def tbm():
    return adsk.fusion.TemporaryBRepManager.get()


def P(p):
    return adsk.core.Point3D.create(p[0] * MM, p[1] * MM, p[2] * MM)


def V(v):
    return adsk.core.Vector3D.create(v[0], v[1], v[2])


def matrix(rows):
    """A Matrix3D from a 3x4 [A | t] in mm (t converted to cm)."""
    m = adsk.core.Matrix3D.create()
    flat = []
    for i in range(3):
        flat += [rows[i][0], rows[i][1], rows[i][2], rows[i][3] * MM]
    flat += [0.0, 0.0, 0.0, 1.0]
    if not m.setWithArray(flat):
        raise RuntimeError("Matrix3D.setWithArray refused %r" % (rows,))
    return m


def m_translate(by):
    return matrix([[1, 0, 0, by[0]], [0, 1, 0, by[1]], [0, 0, 1, by[2]]])


def m_rotate(point, axis, deg):
    m = adsk.core.Matrix3D.create()
    if not m.setToRotation(math.radians(deg), V(axis), P(point)):
        raise RuntimeError("Matrix3D.setToRotation refused")
    return m


def m_scale(c, f):
    return matrix([[f, 0, 0, c[0] * (1 - f)], [0, f, 0, c[1] * (1 - f)], [0, 0, f, c[2] * (1 - f)]])


def m_reflect(o, n):
    ln = math.sqrt(sum(x * x for x in n))
    n = [x / ln for x in n]
    d = sum(o[i] * n[i] for i in range(3))
    return matrix([[(1 if i == j else 0) - 2 * n[i] * n[j] for j in range(3)] + [2 * d * n[i]] for i in range(3)])


BOOL = {"union": adsk.fusion.BooleanTypes.UnionBooleanType,
        "subtract": adsk.fusion.BooleanTypes.DifferenceBooleanType,
        "intersect": adsk.fusion.BooleanTypes.IntersectionBooleanType}


def made(body, what):
    if body is None:
        raise RuntimeError("%s: TemporaryBRepManager returned nothing" % what)
    return body


def apply(model, o):
    """One neutral op on the model (an ordered dict of name -> temporary BRepBody)."""
    k = o["op"]
    T = tbm()
    if k == "box":
        c, s = o["corner"], o["size"]
        ctr = [c[i] + s[i] / 2 for i in range(3)]
        obb = adsk.core.OrientedBoundingBox3D.create(P(ctr), V([1, 0, 0]), V([0, 1, 0]), s[0] * MM, s[1] * MM, s[2] * MM)
        model[o["out"]] = made(T.createBox(obb), "createBox")
    elif k in ("cylinder", "cone"):
        b, a, h = o["base"], o["axis"], o["h"]
        top = [b[i] + h * a[i] for i in range(3)]
        r1, r2 = (o["r"], o["r"]) if k == "cylinder" else (o["r1"], o["r2"])
        model[o["out"]] = made(T.createCylinderOrCone(P(b), r1 * MM, P(top), r2 * MM), "createCylinderOrCone")
    elif k == "sphere":
        model[o["out"]] = made(T.createSphere(P(o["c"]), o["r"] * MM), "createSphere")
    elif k == "torus":
        model[o["out"]] = made(T.createTorus(P(o["c"]), V(o["axis"]), o["R"] * MM, o["r"] * MM), "createTorus")
    elif k == "copy":
        model[o["out"]] = made(T.copy(model[o["src"]]), "copy")
    elif k == "rename":
        model[o["out"]] = model.pop(o["src"])
    elif k in ("translate", "rotate", "scale", "reflect"):
        m = {"translate": lambda: m_translate(o["by"]),
             "rotate": lambda: m_rotate(o["point"], o["axis"], o["deg"]),
             "scale": lambda: m_scale(o["centre"], o["factor"]),
             "reflect": lambda: m_reflect(o["origin"], o["normal"])}[k]()
        names = list(model) if o["bodies"] == "*" else o["bodies"]
        for n in names:
            if not T.transform(model[n], m):
                raise RuntimeError("transform (%s) of %s refused" % (k, n))
    elif k in BOOL:
        target, tool = model[o["target"]], model.pop(o["tool"])
        if not T.booleanOperation(target, tool, BOOL[k]):
            raise RuntimeError("booleanOperation %s of %s by %s refused" % (k, o["target"], o["tool"]))
        if target.faces.count == 0:
            raise RuntimeError("%s of %s by %s: the result is empty" % (k, o["target"], o["tool"]))
    else:
        raise RuntimeError("unknown op %r" % k)


def precise_volumes(bs):
    """Each body's volume at Fusion's very high accuracy, measured on a copy in a scratch, unsaved
    direct-modelling design that is closed without saving. Only the measurement uses the design."""
    app = adsk.core.Application.get()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    try:
        d = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
        d.designType = adsk.fusion.DesignTypes.DirectDesignType
        acc = adsk.fusion.CalculationAccuracy.VeryHighCalculationAccuracy
        return [d.rootComponent.bRepBodies.add(tbm().copy(b)).getPhysicalProperties(acc).volume * 1000.0 for b in bs]
    finally:
        doc.close(False)


def measure(model, probes):
    PC = adsk.fusion.PointContainment
    bs = list(model.values())

    def where(p):
        states = [b.pointContainment(P(p)) for b in bs]
        if any(s == PC.PointInsidePointContainment for s in states):
            return "inside"
        if any(s == PC.PointOnPointContainment for s in states):
            return "on"
        return "outside"

    return {"bodies": sum(b.lumps.count for b in bs), "names": list(model),
            "solid_vols": precise_volumes(bs), "tbm_vols": [b.volume * 1000.0 for b in bs],
            "valid": len(bs) > 0 and all(b.isSolid for b in bs),
            "probes": [where(p) for p in probes]}


def run(_context: str):
    model = {}
    out = {}
    for i, ops in enumerate(STAGES):  # noqa: F821  (the runner appends STAGES, PROBES)
        timed = i == len(STAGES) - 1  # noqa: F821
        t0 = time.time()
        for o in ops:
            try:
                apply(model, o)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError("stage %d, op %s: %s" % (i + 1, json.dumps(o), e))
        if timed:
            out["seconds"] = round(time.time() - t0, 3)
    t1 = time.time()
    out["measured"] = measure(model, PROBES)  # noqa: F821
    out["measure_seconds"] = round(time.time() - t1, 3)
    print(json.dumps(out))
