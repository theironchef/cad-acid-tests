"""Run the acid tests on a solidcraft-cli build, stage by stage, and check each stage against expected/values.py.

usage: python solidcraft/acid.py <path to solidcraft-cli> [acid-1 ...] [--limit 300] [--out results/x.jsonl]

Each stage is checked by running the script up to and including that stage, then:
  inspect.measure       bodies, closed flags and volumes;
  tiny probe cubes      0.0002 mm: a probe counts as inside when at least half its volume overlaps the
                        result (inspect.clearance, with its minimum overlap set to 1e-18 mm^3). For a point
                        0.000005 mm from a surface, the overlap is 47.5% or 52.5%, so the answer is exact.
A stage that does not finish within --limit seconds (on top of the earlier stages' time) is a hang.

SolidCraft's draft angle is signed (positive tilts the face inward) and relative to the face's own taper
on a cone, so the absolute angles in TESTS.md are converted here; the geometry asked for is the same.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "expected"))
import values  # noqa: E402

PROBE = 0.0002
SKIP_PROBES = {}


def C(command, **params):
    return {"command": command, "params": params}


def cyl(r, h, base, axis=None, op="cut", targets=("Part",), name=None, body_name=None):
    p = dict(radius=r, height=h, base=list(base), operation=op)
    if axis is not None:
        p["axis"] = axis
    if op != "new":
        p["targets"] = list(targets)
    if name:
        p["name"] = name
    if body_name:
        p["body_name"] = body_name
    return C("solid.cylinder", **p)


def box(l, w, h, corner=None, center=None, op="new", targets=("Part",), body_name=None):
    p = dict(length=l, width=w, height=h, operation=op)
    if corner is not None:
        p["corner"] = list(corner)
    if center is not None:
        p["center"] = list(center)
    if op != "new":
        p["targets"] = list(targets)
    if body_name:
        p["body_name"] = body_name
    return C("solid.box", **p)


def polar(r, deg, z):
    return [r * math.cos(math.radians(deg)), r * math.sin(math.radians(deg)), z]


# ------------------------------------------------------------------ the tests: one list of commands per stage
def acid1():
    bx = math.sqrt(30 ** 2 - 0.95001 ** 2)
    return [
        [C("solid.torus", major=30, minor=8, operation="new", body_name="Part"),
         box(200, 200, 100, corner=[-100, -100, -100], op="cut")],
        [cyl(0.95, 1.9, [bx, 0.95001, -1.9], op="join")],
        [cyl(0.7, 4, [0, 38, -1])],
        [C("solid.fillet", edges=[[-38, 0, 0], [0, 37.3, 0], [-22, 0, 0]], radius=0.8, body="Part")],
        [C("solid.shell", faces=[[bx, 0.95001, -1.9]], thickness=0.6, direction="inside", body="Part")],
        [cyl(0.5, 40, polar(30, 15 + 30 * k, -20)) for k in range(12)],
        [C("solid.split_body", body="Part", plane={"origin": [0, 0, 4], "normal": [0, 0, 1]})],
        [C("solid.move", bodies=["Part", "Part (1)"], translate=[10000, 0, 0])],
    ]


def acid2():
    return [
        [C("sketch.create", plane="XY", name="Outline"),
         C("sketch.spline.fit_point", points=[list(p) for p in values.SPLINE_PTS], periodic=True),
         C("sketch.finish"),
         C("solid.extrude", sketch="Outline", distance=25, operation="new", body_name="Part")],
        [C("solid.fillet", edges=[[60, 0, 25]], radius=3, body="Part")],
        [cyl(3, 10, [x, y, 25], op="join") for x in (-30, 30) for y in (-12, 12)],
        [C("solid.draft", faces=[[60, 0, 10]], angle=-3, neutral="XY", body="Part")],
        [C("solid.shell", faces=[[0, 0, 0]], thickness=1.5, direction="inside", body="Part")],
        [cyl(2, 50, [x, y, -5]) for x in (-30, 30) for y in (-12, 12)],
        [C("solid.mirror", bodies=["Part"], plane={"origin": [80, 0, 0], "normal": [1, 0, 0]})],
        [box(50, 10, 5, corner=[55, -5, 0], body_name="Bar"),
         C("solid.combine", target="Part", tools=["Part (1)", "Bar"], operation="join")],
    ]


def acid3():
    top = [[40, 0, 30], [80, 30, 30], [40, 60, 30], [0, 30, 30], [1.50001, 1.5, 30], [0.000005, 0, 30]]
    return [
        [box(80, 60, 30, corner=[0, 0, 0], body_name="Part")],
        [box(3, 3, 2, center=[20 + 20 * i, 20 + 20 * j, 31], op="join") for i in range(3) for j in range(2)],
        [cyl(4.917 / 2, 20, [40, 30, 10]),
         C("solid.thread", face=[40 + 4.917 / 2, 30, 25], designation="M6x1", modeled=True, length=15)],
        [cyl(1.25, 12, [40 + 12 * math.cos(math.radians(45 * k)), 30 + 12 * math.sin(math.radians(45 * k)), 20])
         for k in range(8)],
        [cyl(1.5, 6, [1.50001, 0, 26])],
        [C("solid.shell", faces=[], thickness=1.2, direction="inside", body="Part")],
        [C("solid.fillet", edges=top, radius=0.5, body="Part")],
    ]


def acid4():
    mids = [[x, y, 0] for x in (-20, 20) for y in (-20, 20)] + [[x, 0, z] for x in (-20, 20) for z in (-20, 20)] \
        + [[0, y, z] for y in (-20, 20) for z in (-20, 20)]
    return [
        [box(40, 40, 40, center=[0, 0, 0], body_name="Part")],
        [cyl(9, 42, [-21, 0, 0], "X"), cyl(9, 42, [0, -21, 0], "Y"), cyl(9, 42, [0, 0, -21], "Z")],
        [cyl(4, 42, [-21, y, 0], "X") for y in (-10, 10)] + [cyl(4, 42, [x, -21, 1e-5], "Y") for x in (-10, 10)],
        [cyl(5, 60, [26, 0, -30], op="new", body_name="Cyl"), cyl(5, 60, [35.99999, 0, -30], op="join", targets=["Cyl"])],
        [C("solid.sphere", radius=5, center=[26, 0, 34.99999], operation="join", targets=["Cyl"])],
        [C("solid.fillet", edges=mids, radius=2, body="Part", tangent_chain=False)],
    ]


def acid5():
    return [
        [box(30, 20, 10, corner=[1000, 1000, 1000], body_name="Part")],
        [C("solid.fillet", edges=[[x, y, 1005] for x in (1000, 1030) for y in (1000, 1020)], radius=1.3, body="Part")],
        [C("solid.fillet", edges=[[1015, 1000, 1010]], radius=1.29, body="Part")],
        [C("solid.chamfer", edges=[[1015, 1000, 1000]], distance=0.00002, tangent_chain=False, body="Part")],
        [C("solid.fillet", edges=[[1000, 1010, 1000]], radius=0.000025, tangent_chain=False, body="Part")],
        [C("solid.shell", faces=[[1015, 1010, 1000]], thickness=0.4, direction="inside", body="Part")],
    ]


def acid6():
    z0 = 499.99975
    return [
        [C("solid.sphere", radius=500, operation="new", body_name="Part"),
         box(2000, 2000, 1000, corner=[-1000, -1000, z0], op="cut")],
        [cyl(0.06, 0.12, [0, 0, z0], op="join")],
        [C("solid.torus", major=500, minor=0.5, operation="new", body_name="Ring"),
         box(2000, 2000, 10, corner=[-1000, -1000, -10], op="cut", targets=["Ring"]),
         C("solid.move", bodies=["Ring"], translate=[0, 0, 600])],
        [C("solid.shell", faces=[], thickness=0.06, direction="inside", body="Ring")],
        [cyl(0.5, 1000, [0, 0, -1600], op="new", body_name="Needle"),
         C("solid.draft", faces=[[0.5, 0, -1100]], angle=0.02, neutral={"origin": [0, 0, -1600], "normal": [0, 0, 1]},
           body="Needle")],
        [cyl(0.4, 200, [0, 0, -600], op="new", body_name="Rod"),
         C("solid.combine", target="Part", tools=["Rod", "Needle"], operation="join")],
    ]


def acid7():
    taper = math.degrees(math.atan(60 / 200))  # the cone's own taper, 16.6992 degrees
    r_top = 100 - 200 * math.tan(math.radians(18))
    rim = [polar(r_top, 30 + 60 * k, 200) for k in range(6)] + [polar(27, 60 * k, 200) for k in range(6)]
    return [
        [C("solid.cone", radius=100, top_radius=40, height=200, operation="new", body_name="Part")],
        [cyl(5, 51, polar(32, 60 * k, 150)) for k in range(6)],
        [C("solid.draft", faces=[[70, 0, 100]], angle=18 - taper, neutral="XY", body="Part")],
        [C("solid.fillet", edges=rim, radius=0.5, body="Part")],
        [cyl(60, 81, [0, 0, -1])],
        [C("solid.shell", faces=[[80, 0, 0]], thickness=2, direction="inside", body="Part")],
    ]


def acid8():
    return [
        [cyl(50, 120, [0, 0, 0], op="new", body_name="Part")],
        [cyl(3, 20, [50, 0, 20], [-1, 0, 0], name="Hole1"),
         C("solid.pattern.circular", features=["Hole1"], axis="Z", count=24, name="Ring1")],
        [C("solid.pattern.rectangular", features=["Hole1", "Ring1"], dir1=[0, 0, 1], count1=5, spacing1=20)],
        [cyl(2, 3, polar(30, 15 * k, 120), op="join") for k in range(7)],
        [C("solid.shell", faces=[], thickness=2, direction="inside", body="Part")],
        [C("solid.split_body", body="Part", plane={"origin": [0, 0, 61], "normal": [0, 0, 1]})],
        [C("solid.mirror", bodies=["@upper"], plane={"origin": [0, 0, 140], "normal": [0, 0, 1]})],
    ]


def _tower_edge_points():
    """Points on the edges where the sphere (r 55 at (0, 0, 50)) meets the four twisted faces: on each
    face, near both ends of its bottom and top edges, found by bisection along the ruling."""
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
                    continue  # the sphere does not reach this end
                for _ in range(80):
                    mid = (lo + hi) / 2
                    lo, hi = (mid, hi) if d(P(i, u, mid)) > 0 else (lo, mid)
                pts.append([round(c, 9) for c in P(i, u, (lo + hi) / 2)])
    return pts


def acid9():
    sq = lambda z: [[round(x, 12), round(y, 12)] for x, y in values.tower_section(z)]
    return [
        [C("sketch.create", plane="XY", name="S0"), C("sketch.line", points=sq(0), closed=True), C("sketch.finish"),
         C("sketch.create", plane={"origin": [0, 0, 100], "x_dir": [1, 0, 0], "y_dir": [0, 1, 0]}, name="S1"),
         C("sketch.line", points=sq(100), closed=True), C("sketch.finish"),
         C("solid.loft", sections=[{"sketch": "S0"}, {"sketch": "S1"}], operation="new", body_name="Part")],
        [cyl(36.94 / 2, 102, [0, 0, -1])],
        [C("solid.sphere", radius=55, center=[0, 0, 50], operation="intersect", targets=["Part"])],
        [C("solid.fillet", edges=_tower_edge_points(), radius=1, body="Part")],
        [C("solid.shell", faces=[[0, 22, 100]], thickness=1, direction="inside", body="Part")],
        [C("solid.split_body", body="Part", plane={"origin": [0, 0, 0], "normal": [1, -1, 0]})],
    ]


def acid10():
    return [
        [box(50, 50, 50, corner=[0, 0, 0], body_name="Part")],
        [cyl(10, 50, [0, 25, 59.99999], "X", op="join")],
        [C("solid.sphere", radius=15, center=[64.99999, 25, 25], operation="join", targets=["Part"])],
        [box(10, 1.00001, 52, corner=[20, -1, -1], op="cut")],
        [C("solid.fillet", edges=[[x, y, 25] for x in (0, 50) for y in (0, 50)], radius=5, tangent_chain=False,
           body="Part")],
        [C("solid.move", bodies=["Part"], translate=[1000000, 1000000, 0])],
    ]


BUILDERS = {"acid-1": acid1, "acid-2": acid2, "acid-3": acid3, "acid-4": acid4, "acid-5": acid5,
            "acid-6": acid6, "acid-7": acid7, "acid-8": acid8, "acid-9": acid9, "acid-10": acid10}


# ------------------------------------------------------------------ running and checking
def run_cli(cli, cmds, timeout, tmp):
    path, out = os.path.join(tmp, "s.json"), os.path.join(tmp, "s.out.json")
    if os.path.exists(out):
        os.remove(out)
    json.dump({"commands": cmds}, open(path, "w", encoding="utf-8"))
    t0 = time.time()
    try:
        p = subprocess.run([cli, "run", path, "--param-check", "strict", "--results", "--json-out", out],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout", time.time() - t0
    if p.returncode != 0:
        msg = next((l for l in (p.stderr + p.stdout).splitlines() if l.startswith("error")), p.stderr.strip()[-300:])
        return None, msg.strip()[:400], time.time() - t0
    return json.load(open(out, encoding="utf-8")), None, time.time() - t0


def resolve_upper(cmds_so_far, cli, tmp):
    """acid-8 stage 7 mirrors the upper of the two bodies the split made: find its name."""
    res, err, _ = run_cli(cli, cmds_so_far + [C("inspect.measure")], 600, tmp)
    if err:
        return None
    bodies = res[-1]["bodies"]
    return max(bodies, key=lambda b: b["bbox"]["max"][2])["name"]


def check(test, stage, res, n_cmds, prev_volume, cli_stage_ms):
    exp = values.TESTS[test][stage]
    meas = res[n_cmds]  # the inspect.measure right after the stage
    bodies = [b for b in meas["bodies"] if not b["name"].startswith("probe_")]
    closed = all(b.get("closed") for b in bodies)
    vol = sum(b.get("volume_mm3") or 0 for b in bodies)
    m = {"bodies": len(bodies), "volume_mm3": vol, "valid": bool(closed)}
    bad = []
    if not closed:
        bad.append("not a closed solid")
    if "bodies" in exp and m["bodies"] != exp["bodies"]:
        bad.append("%d bodies, expected %d" % (m["bodies"], exp["bodies"]))
    v = exp.get("vol")
    if v:
        slack = values.DELTA_SLACK_REL * abs(prev_volume or 0)
        if v[0] == "exact" and abs(vol - v[1]) > values.EXACT_REL * max(1.0, abs(v[1])):
            bad.append("volume %.6f, expected %.6f" % (vol, v[1]))
        elif v[0] == "window" and not v[1] <= vol <= v[2]:
            bad.append("volume %.6f outside [%.6f, %.6f]" % (vol, v[1], v[2]))
        elif v[0] == "delta" and prev_volume is not None and not v[1] - slack <= vol - prev_volume <= v[2] + slack:
            bad.append("volume change %.6f outside [%.6f, %.6f]" % (vol - prev_volume, v[1], v[2]))
    for sv in exp.get("solid_vol", []):
        if not any(abs((b.get("volume_mm3") or 0) - sv) <= values.EXACT_REL * max(1.0, sv) for b in bodies):
            bad.append("no solid of volume %.6f" % sv)
    inter = res[-1]
    over = {}
    for i in inter.get("overlaps", []):
        for a, b in ((i["a"], i["b"]), (i["b"], i["a"])):
            if a.startswith("probe_") and not b.startswith("probe_"):
                over[a] = over.get(a, 0) + i.get("volume_mm3", 0)
    skipped = []
    for kind, pts in (("inside", exp.get("inside", [])), ("outside", exp.get("outside", []))):
        for j, p in enumerate(pts):
            key = tuple(round(c, 6) for c in p)
            if key in {tuple(round(c, 6) for c in q) for q in SKIP_PROBES.get(test, ())}:
                skipped.append(key)
                continue
            is_in = over.get("probe_%s_%d" % (kind, j), 0) >= 0.5 * PROBE ** 3
            if is_in != (kind == "inside"):
                bad.append("%r should be %s" % (key, kind))
    return m, bad, skipped


def probes_for(test, stage):
    exp = values.TESTS[test][stage]
    cmds = []
    for kind in ("inside", "outside"):
        for j, p in enumerate(exp.get(kind, [])):
            cmds.append(box(PROBE, PROBE, PROBE, center=list(p), body_name="probe_%s_%d" % (kind, j)))
    names = [c["params"]["body_name"] for c in cmds]
    return cmds + ([C("inspect.clearance", against=names, min_volume=1e-18)] if names else [C("inspect.measure")])


def run_test(cli, test, version, out, limit, tmp):
    stages = BUILDERS[test]()
    so_far, prev_v, spent = [], None, 0.0
    for n, stage_cmds in enumerate(stages, 1):
        if any(c["params"].get("bodies") == ["@upper"] for c in stage_cmds):
            up = resolve_upper(so_far, cli, tmp)
            stage_cmds = [dict(c, params=dict(c["params"], bodies=[up])) if c["params"].get("bodies") == ["@upper"]
                          else c for c in stage_cmds]
        cmds = so_far + stage_cmds
        res, err, secs = run_cli(cli, cmds + [C("inspect.measure")] + probes_for(test, n), limit + spent * 2 + 60, tmp)
        rec = {"pipeline": "solidcraft", "version": version, "test": test, "stage": n}
        if err == "timeout":
            rec.update(outcome="hang", seconds=limit, detail="no result within %d s" % limit)
        elif err:
            rec.update(outcome="error", seconds=round(secs, 3), detail=err)
        else:
            stage_ms = sum(r.get("ms", 0) or 0 for r in res[len(so_far):len(cmds)] if isinstance(r, dict))
            m, bad, skipped = check(test, n, res, len(cmds), prev_v, stage_ms)
            rec.update(outcome="pass" if not bad else "fail", seconds=round(stage_ms / 1000, 3), measured=m,
                       detail="; ".join(bad))
            if skipped:
                rec["probes_skipped"] = [list(k) for k in skipped]
            prev_v = m["volume_mm3"]
            spent += stage_ms / 1000
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        print(test, n, rec["outcome"], rec.get("seconds"), (rec.get("detail") or "")[:160], flush=True)
        if rec["outcome"] in ("error", "hang"):
            break
        so_far = cmds


def main(argv):
    cli, args = argv[0], argv[1:]
    limit, out = 300, None
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1])
        del args[i:i + 2]
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1]
        del args[i:i + 2]
    ver = subprocess.run([cli, "--version"], capture_output=True, text=True).stdout.strip().split()[-1]
    version = "SolidCraft " + ver
    out = os.path.abspath(out or os.path.join(ROOT, "results", "solidcraft-%s.jsonl" % ver))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for t in args or list(BUILDERS):
            run_test(cli, t, version, out, limit, tmp)
    print("results:", out)


if __name__ == "__main__":
    main(sys.argv[1:])
