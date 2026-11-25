"""The acid tests as neutral kernel operations: one spec that every kernel-only harness runs, op for op.

The kernel-near Fusion harness (tools/run_fusion_kernel.py, Fusion's TemporaryBRepManager) and the
kernel-only SolidCraft harness (solidcore/, solidcore-ops) both execute exactly this list: the same
operations, in the same order, with the same numbers. Every number is worked out here, in Python, from
the dimensions in TESTS.md, so both harnesses consume identical floats (cos 15 degrees included).
docs/kernel-ops.md maps each op to the Fusion call and the solidcore-ops call.

The op set is the intersection of what both kernels can do directly at the B-rep level, without
features, sketches or history. A stage that needs anything outside it is marked
"not_expressible": [the missing ops], and is "not run" on both rows. Since a stage builds on the one
before it, every stage after a not-run stage is "not run" too (see stage()).

Model. A stage acts on a set of named bodies, which persists from stage to stage. Ops:
  box       out, corner, size            axis-aligned box from its lower corner, size > 0
  cylinder  out, base, axis, r, h        base centre, unit axis, radius, height
  cone      out, base, axis, r1, r2, h   frustum: radius r1 at base, r2 at base + h*axis (axis is +Z)
  sphere    out, c, r
  torus     out, c, axis, R, r           ring torus, major R, minor r (axis is +Z)
  copy      src, out
  translate bodies, by                  bodies: a list of names, or "*" for every body
  rotate    bodies, point, axis, deg    right-handed about the line through point along axis
  scale     bodies, centre, factor      uniform
  reflect   bodies, origin, normal      mirror in the plane through origin square to normal
  union | subtract | intersect   target, tool   target := target op tool; the tool body is consumed
                                                 (removed from the model); an empty result is an error
  rename    src, out
Tool bodies made in a stage are consumed in it, so what is left at the end of a stage is the model.

What a stage measures, on every body left in the model:
  bodies   the number of solids (lumps / material census), summed over the bodies
  valid    every body is a closed solid (and passes the kernel's own check, where it has one)
  volume   the sum of the bodies' volumes; solid_vols the volume of each
  probes   values.TESTS[test][stage]'s inside then outside points, each classified exactly by the
           kernel (inside / outside / on the boundary); a point the kernel puts "on" fails its probe
The checks are values.TESTS[test][stage], with values.EXACT_REL and values.DELTA_SLACK_REL, as in every
other harness.

python expected/kernel_ops.py           a summary: each stage's ops count, or what it lacks
python expected/kernel_ops.py --json    the whole spec as JSON (for the Rust harness)
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import values  # noqa: E402

SPEC_VERSION = 1
X, Y, Z = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]

# The ops a stage may use: both kernels have each one at the B-rep level.
OPS = ("box", "cylinder", "cone", "sphere", "torus", "copy", "translate", "rotate", "scale", "reflect",
       "union", "subtract", "intersect", "rename")

# Why an op is outside the intersection, for the not_expressible lists and the docs.
MISSING = {
    "round": "round (rolling-ball blend): TemporaryBRepManager has no blend; solidcore-ops has blend()",
    "chamfer": "chamfer: TemporaryBRepManager has no blend; solidcore-ops has blend(Chamfer)",
    "shell": "shell (inward offset of a solid): TemporaryBRepManager has no offset; solidcore-ops has shell()",
    "draft": "draft (face taper about a neutral plane): TemporaryBRepManager has none; solidcore-ops has draft()",
    "extrude": "extrude of a planar spline profile: TemporaryBRepManager has no extrude, and createWireFromCurves "
               "takes no NURBS curve; solidcore-ops has sweep::extrude()",
    "loft": "loft with straight rulings into a solid: TemporaryBRepManager.createRuledSurface gives only a sheet and "
            "it has no stitch to close it with createFaceFromPlanarWires caps (a union of the sheets stays a sheet); "
            "solidcore-ops has sweep::loft()",
    "thread": "modelled thread (helical sweep): TemporaryBRepManager has createHelixWire but no sweep; "
              "solidcore-ops has screw_sweep()",
    "joint": "revolute joint, gear and driven motion: assembly constraints, in neither kernel",
}


# ------------------------------------------------------------------ op builders (plain dicts)
def f3(p):
    return [float(c) for c in p]


def box(out, corner, size):
    return {"op": "box", "out": out, "corner": f3(corner), "size": f3(size)}


def box2(out, lo, hi):
    """A box between two corners (TESTS.md's 'from ... to ...')."""
    return box(out, lo, [hi[i] - lo[i] for i in range(3)])


def cylinder(out, base, r, h, axis=Z):
    return {"op": "cylinder", "out": out, "base": f3(base), "axis": f3(axis), "r": float(r), "h": float(h)}


def cone(out, base, r1, r2, h):
    return {"op": "cone", "out": out, "base": f3(base), "axis": Z, "r1": float(r1), "r2": float(r2), "h": float(h)}


def sphere(out, c, r):
    return {"op": "sphere", "out": out, "c": f3(c), "r": float(r)}


def torus(out, c, R, r):
    return {"op": "torus", "out": out, "c": f3(c), "axis": Z, "R": float(R), "r": float(r)}


def copy(src, out):
    return {"op": "copy", "src": src, "out": out}


def translate(bodies, by):
    return {"op": "translate", "bodies": bodies, "by": f3(by)}


def rotate(bodies, point, axis, deg):
    return {"op": "rotate", "bodies": bodies, "point": f3(point), "axis": f3(axis), "deg": float(deg)}


def scale(bodies, centre, factor):
    return {"op": "scale", "bodies": bodies, "centre": f3(centre), "factor": float(factor)}


def reflect(bodies, origin, normal):
    return {"op": "reflect", "bodies": bodies, "origin": f3(origin), "normal": f3(normal)}


def boolean(op, target, tool):
    return {"op": op, "target": target, "tool": tool}


def union(target, tool):
    return boolean("union", target, tool)


def subtract(target, tool):
    return boolean("subtract", target, tool)


def intersect(target, tool):
    return boolean("intersect", target, tool)


def rename(src, out):
    return {"op": "rename", "src": src, "out": out}


def with_tool(op, target, make):
    """[make the tool "T"] + [target op T]."""
    return [make("T"), boolean(op, target, "T")]


def polar(r, deg, z):
    a = math.radians(deg)
    return [r * math.cos(a), r * math.sin(a), z]


def split_z(name, z, half=1000.0):
    """Split body `name` by the plane Z = z, keeping both: a copy, then each intersected with the half
    space on its side (a box). The lower part keeps the name; the upper is "<name> (1)"."""
    up = name + " (1)"
    return [copy(name, up),
            box("T", [-half, -half, z - 2 * half], [2 * half, 2 * half, 2 * half]), intersect(name, "T"),
            box("T", [-half, -half, z], [2 * half, 2 * half, 2 * half]), intersect(up, "T")]


def cat(lists):
    out = []
    for item in lists:
        out += item
    return out


def ne(*keys):
    return {"not_expressible": [MISSING[k] for k in keys]}


def ops(*lists, note=None):
    out = []
    for item in lists:
        out += item if isinstance(item, list) else [item]
    d = {"ops": out}
    if note:
        d["note"] = note
    return d


# ------------------------------------------------------------------ the tests
def acid1(name="Part"):
    bx = math.sqrt(30 ** 2 - 0.95001 ** 2)
    return {
        1: ops(torus(name, [0, 0, 0], 30, 8), with_tool("subtract", name, lambda t: box(t, [-100, -100, -100], [200, 200, 100]))),
        2: ops(with_tool("union", name, lambda t: cylinder(t, [bx, 0.95001, -1.9], 0.95, 1.9))),
        3: ops(with_tool("subtract", name, lambda t: cylinder(t, [0, 38, -1], 0.7, 4))),
        4: ne("round"),
        5: ne("shell"),
        6: ops(cat(with_tool("subtract", name, lambda t, k=k: cylinder(t, polar(30, 15 + 30 * k, -20), 0.5, 40)) for k in range(12))),
        7: ops(split_z(name, 4)),
        8: ops(translate("*", [10000, 0, 0])),
    }


def acid2():
    bosses = [(x, y) for x in (-30, 30) for y in (-12, 12)]
    return {
        1: ne("extrude"),
        2: ne("round"),
        3: ops(cat(with_tool("union", "Part", lambda t, x=x, y=y: cylinder(t, [x, y, 25], 3, 10)) for x, y in bosses)),
        4: ne("draft"),
        5: ne("shell"),
        6: ops(cat(with_tool("subtract", "Part", lambda t, x=x, y=y: cylinder(t, [x, y, -5], 2, 50)) for x, y in bosses)),
        7: ops(copy("Part", "Part (1)"), reflect(["Part (1)"], [80, 0, 0], X)),
        8: ops(with_tool("union", "Part", lambda t: box2(t, [55, -5, 0], [105, 5, 5])), union("Part", "Part (1)")),
    }


def acid3():
    pads = []
    for i in range(3):
        for j in range(2):
            cx, cy = 20 + 20 * i, 20 + 20 * j
            pads += with_tool("union", "Part", lambda t, cx=cx, cy=cy: box(t, [cx - 1.5, cy - 1.5, 30], [3, 3, 2]))
    return {
        1: ops(box("Part", [0, 0, 0], [80, 60, 30])),
        2: ops(pads),
        3: ne("thread"),
        4: ops(cat(with_tool("subtract", "Part", lambda t, k=k: cylinder(t, [40 + 12 * math.cos(math.radians(45 * k)),
                                                                              30 + 12 * math.sin(math.radians(45 * k)), 20], 1.25, 12)) for k in range(8))),
        5: ops(with_tool("subtract", "Part", lambda t: cylinder(t, [1.50001, 0, 26], 1.5, 6))),
        6: ne("shell"),
        7: ne("round"),
    }


def acid4():
    return {
        1: ops(box("Part", [-20, -20, -20], [40, 40, 40])),
        2: ops(with_tool("subtract", "Part", lambda t: cylinder(t, [-21, 0, 0], 9, 42, X)),
               with_tool("subtract", "Part", lambda t: cylinder(t, [0, -21, 0], 9, 42, Y)),
               with_tool("subtract", "Part", lambda t: cylinder(t, [0, 0, -21], 9, 42, Z))),
        3: ops(cat(with_tool("subtract", "Part", lambda t, y=y: cylinder(t, [-21, y, 0], 4, 42, X)) for y in (-10, 10))
               + cat(with_tool("subtract", "Part", lambda t, x=x: cylinder(t, [x, -21, 1e-5], 4, 42, Y)) for x in (-10, 10))),
        4: ops(cylinder("Cyl", [26, 0, -30], 5, 60),
               with_tool("union", "Cyl", lambda t: cylinder(t, [35.99999, 0, -30], 5, 60))),
        5: ops(with_tool("union", "Cyl", lambda t: sphere(t, [26, 0, 34.99999], 5))),
        6: ne("round"),
    }


def acid5():
    return {1: ops(box("Part", [1000, 1000, 1000], [30, 20, 10])), 2: ne("round"), 3: ne("round"),
            4: ne("chamfer"), 5: ne("round"), 6: ne("shell")}


def acid6():
    z0 = 499.99975
    return {
        1: ops(sphere("Part", [0, 0, 0], 500), with_tool("subtract", "Part", lambda t: box(t, [-1000, -1000, z0], [2000, 2000, 1000]))),
        2: ops(with_tool("union", "Part", lambda t: cylinder(t, [0, 0, z0], 0.06, 0.12))),
        3: ops(torus("Ring", [0, 0, 0], 500, 0.5),
               with_tool("subtract", "Ring", lambda t: box(t, [-1000, -1000, -10], [2000, 2000, 10])),
               translate(["Ring"], [0, 0, 600])),
        4: ne("shell"),
        5: ne("draft"),
        6: ops(with_tool("union", "Part", lambda t: cylinder(t, [0, 0, -600], 0.4, 200)), union("Part", "Needle")),
    }


def acid7():
    return {
        1: ops(cone("Part", [0, 0, 0], 100, 40, 200)),
        2: ops(cat(with_tool("subtract", "Part", lambda t, k=k: cylinder(t, polar(32, 60 * k, 150), 5, 51)) for k in range(6))),
        3: ne("draft"),
        4: ne("round"),
        5: ops(with_tool("subtract", "Part", lambda t: cylinder(t, [0, 0, -1], 60, 81))),
        6: ne("shell"),
    }


def hole_ring(z):
    """A ring of 24 radial holes at height z, cut one by one (the pattern's instances, in order)."""
    out = []
    for k in range(24):
        a = math.radians(15 * k)
        out += with_tool("subtract", "Part", lambda t, a=a: cylinder(t, [50 * math.cos(a), 50 * math.sin(a), z], 3, 20,
                                                                     [-math.cos(a), -math.sin(a), 0.0]))
    return out


def acid8():
    return {
        1: ops(cylinder("Part", [0, 0, 0], 50, 120)),
        2: ops(hole_ring(20)),
        3: ops(sum((hole_ring(z) for z in (40, 60, 80, 100)), [])),
        4: ops(cat(with_tool("union", "Part", lambda t, k=k: cylinder(t, polar(30, 15 * k, 120), 2, 3)) for k in range(7))),
        5: ne("shell"),
        6: ops(split_z("Part", 61)),
        # the upper body of the split is "Part (1)"
        7: ops(copy("Part (1)", "Part (2)"), reflect(["Part (2)"], [0, 0, 140], Z)),
    }


def acid9():
    half = 1000.0
    return {
        1: ne("loft"),
        2: ops(with_tool("subtract", "Part", lambda t: cylinder(t, [0, 0, -1], 36.94 / 2, 102))),
        3: ops(with_tool("intersect", "Part", lambda t: sphere(t, [0, 0, 50], 55))),
        4: ne("round"),
        5: ne("shell"),
        # the vertical plane through Z at 45 degrees (normal (1, -1, 0)): each half space is a box on one side of
        # the XZ plane, turned 45 degrees about Z
        6: ops(copy("Part", "Part (1)"),
               box("T", [-half, -2 * half, -half], [2 * half, 2 * half, 2 * half]), rotate(["T"], [0, 0, 0], Z, 45),
               intersect("Part", "T"),
               box("T", [-half, 0, -half], [2 * half, 2 * half, 2 * half]), rotate(["T"], [0, 0, 0], Z, 45),
               intersect("Part (1)", "T"), note="split as two intersections with half-space boxes"),
    }


def acid10():
    return {
        1: ops(box("Part", [0, 0, 0], [50, 50, 50])),
        2: ops(with_tool("union", "Part", lambda t: cylinder(t, [0, 25, 59.99998], 10, 50, X))),
        3: ops(with_tool("union", "Part", lambda t: sphere(t, [64.99998, 25, 25], 15))),
        4: ops(with_tool("subtract", "Part", lambda t: box2(t, [20, -1, -1], [30, 0.00002, 51]))),
        5: ne("round"),
        6: ops(translate("*", [1000000, 1000000, 0])),
    }


def acid11():
    ring = sum((acid1("Ring")[n]["ops"] for n in (1, 2, 3)), [])
    joint = "placed by its move; the revolute joint is an assembly constraint, not run"
    return {
        1: ops(box("Base", [-80, -50, 0], [160, 100, 10]),
               with_tool("union", "Base", lambda t: cylinder(t, [-40, 0, 10], 3, 2)),
               with_tool("union", "Base", lambda t: cylinder(t, [13.5, 0, 10], 3, 2))),
        2: ops(ring, translate(["Ring"], [-40, 0, 30]), note="the ring is acid-1 stages 1-3; " + joint),
        3: {"not_expressible": ["the cam is acid-2 at stage 3: " + MISSING["extrude"], MISSING["round"]]},
        4: ne("joint"),
        5: ne("joint"),
        6: {"not_expressible": ["interference through the driven motion: needs the cam (stage 3) and the motion (stage 5)"]},
    }


_BUILD = {"acid-1": acid1, "acid-2": acid2, "acid-3": acid3, "acid-4": acid4, "acid-5": acid5, "acid-6": acid6,
          "acid-7": acid7, "acid-8": acid8, "acid-9": acid9, "acid-10": acid10, "acid-11": acid11}
SPEC = {t: f() for t, f in _BUILD.items()}


def _check_spec():
    for t, stages in SPEC.items():
        assert sorted(stages) == sorted(values.TESTS[t]), t
        for n, s in stages.items():
            assert ("ops" in s) != ("not_expressible" in s), (t, n)
            for o in s.get("ops", []):
                assert o["op"] in OPS, (t, n, o)


_check_spec()


def stage(test, n):
    """Stage n of the test, as a harness runs it:
      {"run": True, "ops": [...], "note": ..., "checks": values.TESTS[test][n], "probes": [...]}
    or {"run": False, "detail": why}: it needs an op outside the intersection, or it builds on a stage
    that is not run. "probes" is the inside points then the outside points, each with its expected class."""
    for k in range(1, n):
        if "not_expressible" in SPEC[test][k]:
            return {"run": False, "detail": "builds on stage %d, which is not run" % k}
    s = SPEC[test][n]
    if "not_expressible" in s:
        return {"run": False, "detail": "needs " + "; ".join(s["not_expressible"])}
    chk = values.TESTS[test][n]
    probes = [{"p": f3(p), "want": "inside"} for p in chk.get("inside", [])] + \
             [{"p": f3(p), "want": "outside"} for p in chk.get("outside", [])]
    return {"run": True, "ops": s["ops"], "note": s.get("note"), "checks": chk, "probes": probes}


def as_json():
    return {"spec_version": SPEC_VERSION, "ops": list(OPS),
            "tests": {t: {str(n): dict(stage(t, n), expressible="ops" in SPEC[t][n]) for n in SPEC[t]} for t in SPEC}}


if __name__ == "__main__":
    if "--json" in sys.argv:
        json.dump(as_json(), sys.stdout, indent=1)
        sys.exit()
    for t in SPEC:
        row = []
        for n in SPEC[t]:
            st = stage(t, n)
            row.append("%d:%s" % (n, ("%d ops" % len(st["ops"])) if st["run"] else
                                  ("needs " + (",".join(k for k, v in MISSING.items() if v in SPEC[t][n]["not_expressible"]) or "other"))
                                  if "not_expressible" in SPEC[t][n] else "blocked"))
        print("%-8s %s" % (t, "  ".join(row)))
