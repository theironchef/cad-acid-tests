"""The renderer shared by the sketches (tools/reference.py) and the harnesses' renders of finished output.

Both kinds of picture go through the same camera, lighting and outlines, so a sketch and a program's render
of the same test can be compared side by side:
  render_sdf(f, view)      sphere-traces an implicit solid f(x, y, z) -> signed distance (numpy arrays)
  render_mesh(tris, view)  rasterizes a triangle mesh (an (n, 3, 3) array, e.g. from read_stl) with a z-buffer
Each test's view (direction, framing box, cut-away, zoom insets) is in VIEWS, in the test's own local frame:
a program's mesh is moved by the view's "offset" first (acid-1 is built 10 m out, acid-10 1 km out).

A harness calls render_completion() once a test has passed every stage. It needs numpy and Pillow; without
them it says so and returns, and the harness carries on.

python tools/render.py <test> <program version> <mesh.stl> [...]   renders a mesh as that program's picture
"""
import math
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "docs", "img")
MESHES = os.path.join(ROOT, "meshes")  # gitignored: exported meshes are not committed
W, H, SS = 640, 480, 2                  # output size and supersampling

# Per test, in its local frame: the box to frame (lo, hi), the view direction (azimuth, elevation, degrees: the
# camera sits in that direction from the part), a cut-away box removed from the picture to show what is inside,
# and zoom insets for features too small to see whole.
VIEWS = {
    "acid-1": dict(lo=(-38, -38, -2), hi=(38, 38, 8), az=-125, el=32, offset=(10000, 0, 0),
                   cut=((-40, -40, -5), (0, 0, 12))),
    "acid-2": dict(lo=(-64, -40, 0), hi=(224, 43, 35), az=-70, el=35),
    "acid-3": dict(lo=(0, 0, 0), hi=(80, 60, 32), az=-60, el=38, cut=((40, -1, -1), (81, 30, 40))),
    "acid-4": dict(lo=(-20, -20, -30), hi=(41, 20, 40), az=-55, el=28),
    "acid-5": dict(lo=(0, 0, 0), hi=(30, 20, 10), az=-60, el=30, offset=(1000, 1000, 1000),
                   cut=((15, -1, -1), (31, 10, 11))),
    "acid-6": dict(lo=(-505, -505, -1600), hi=(505, 505, 601), az=-60, el=12, insets=[
        dict(lo=(-0.35, -0.35, 499.9), hi=(0.35, 0.35, 500.13), az=-60, el=25, rect=(440, 12, 188, 141),
             label="pole: the 0.12 mm pin on the 1 mm flat"),
        dict(lo=(499.3, -0.05, 599.9), hi=(500.7, 0.6, 600.6), az=-90, el=8, rect=(440, 165, 188, 141),
             cut=((480, -10, 590), (520, 0, 610)), label="ring tube, cut open: the 0.06 mm wall"),
        dict(lo=(-0.6, -0.6, -603), hi=(0.6, 0.6, -597), az=-60, el=12, rect=(440, 318, 188, 141),
             label="needle tip meets the 0.8 mm rod"),
    ]),
    "acid-7": dict(lo=(-100, -100, 0), hi=(100, 100, 200), az=-60, el=30, cut=((0, -101, -1), (101, 0, 201))),
    "acid-8": dict(lo=(-52, -52, 0), hi=(52, 52, 219), az=-60, el=22, cut=((0, -60, -1), (60, 0, 230))),
    "acid-9": dict(lo=(-29, -29, 0), hi=(29, 29, 100), az=-60, el=28),
    "acid-10": dict(lo=(0, -1, 0), hi=(80, 50, 70), az=-55, el=28, offset=(1000000, 1000000, 0)),
    "acid-11": dict(lo=(-80, -50, 0), hi=(80, 50, 38), az=-65, el=35),
}

BG = (0.957, 0.949, 0.925)
BASE = (0.60, 0.68, 0.79)
CAP = (0.80, 0.60, 0.52)
INK = (0.17, 0.17, 0.17)


def slug(version):
    """A program version as a file name: 'SolidCraft 0.50.0 nightly 2026-11-26' -> 'solidcraft-0.50.0-nightly-2026-11-26'."""
    return re.sub(r"[^a-z0-9.]+", "-", version.lower()).strip("-.")


def completion_png(test, version):
    return os.path.join(IMG, "%s-%s.png" % (test, slug(version)))


def full_pass(results_path, test, version, stages):
    """True when the results file's latest record of each of the test's stages, for this version, is a pass."""
    import json
    last = {}
    if not os.path.exists(results_path):
        return False
    for line in open(results_path, encoding="utf-8"):
        r = json.loads(line)
        if r.get("test") == test and r.get("version", version) == version:
            last[r["stage"]] = r["outcome"]
    return all(last.get(k) == "pass" for k in range(1, stages + 1))


# ------------------------------------------------------------------ camera
def _camera(np, v, w, h):
    lo, hi = np.array(v["lo"], float), np.array(v["hi"], float)
    az, el = math.radians(v["az"]), math.radians(v["el"])
    d = -np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    right = np.cross(d, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, d)
    c = (lo + hi) / 2
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]) - c
    eu, ev = np.abs(corners @ right).max(), np.abs(corners @ up).max()
    px = max(2 * eu / (w * 0.9), 2 * ev / (h * 0.9))
    return dict(c=c, d=d, right=right, up=up, px=px, w=w, h=h, R=0.5 * np.linalg.norm(hi - lo) * 1.05 + 4 * px)


def _box_sdf(np, x, y, z, lo, hi):
    qx = np.maximum(lo[0] - x, x - hi[0])
    qy = np.maximum(lo[1] - y, y - hi[1])
    qz = np.maximum(lo[2] - z, z - hi[2])
    out = np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2 + np.maximum(qz, 0) ** 2)
    return out + np.minimum(np.maximum(qx, np.maximum(qy, qz)), 0)


# ------------------------------------------------------------------ G-buffers: mask, depth, normal (toward the camera), cap
def _gbuffer_sdf(np, f, cam, cut):
    w, h, px = cam["w"], cam["h"], cam["px"]
    if cut is not None:
        g = f
        f = lambda x, y, z: np.maximum(g(x, y, z), -_box_sdf(np, x, y, z, *cut))
    jj, ii = np.mgrid[0:h, 0:w]
    u = (ii.ravel() + 0.5 - w / 2) * px
    vv = (h / 2 - jj.ravel() - 0.5) * px
    o = cam["c"] + u[:, None] * cam["right"] + vv[:, None] * cam["up"] - cam["d"] * cam["R"]
    d = cam["d"]
    t = np.zeros(len(u))
    hit = np.zeros(len(u), bool)
    act = np.arange(len(u))
    eps, tmax = 0.35 * px, 2 * cam["R"]
    for _ in range(600):
        if not len(act):
            break
        p = o[act] + t[act, None] * d
        dist = f(p[:, 0], p[:, 1], p[:, 2])
        done = dist < eps
        hit[act[done]] = True
        t[act] += np.where(done, 0, np.maximum(dist * 0.9, eps * 0.25))
        act = act[~done & (t[act] < tmax)]
    idx = np.nonzero(hit)[0]
    p = o[idx] + t[idx, None] * d
    e = px * 0.5
    n = np.stack([f(p[:, 0] + e, p[:, 1], p[:, 2]) - f(p[:, 0] - e, p[:, 1], p[:, 2]),
                  f(p[:, 0], p[:, 1] + e, p[:, 2]) - f(p[:, 0], p[:, 1] - e, p[:, 2]),
                  f(p[:, 0], p[:, 1], p[:, 2] + e) - f(p[:, 0], p[:, 1], p[:, 2] - e)], 1)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
    cap = np.zeros(len(idx), bool)
    if cut is not None:  # a point on the cut-away box's face is a cut through material
        cap = np.abs(_box_sdf(np, p[:, 0], p[:, 1], p[:, 2], *cut)) < 2 * eps
    N = w * h
    depth, normal, caps = np.full(N, np.inf), np.zeros((N, 3)), np.zeros(N, bool)
    depth[idx], normal[idx], caps[idx] = t[idx], n, cap
    return hit.reshape(h, w), depth.reshape(h, w), normal.reshape(h, w, 3), caps.reshape(h, w)


def _gbuffer_mesh(np, tris, cam, cut):
    w, h, px = cam["w"], cam["h"], cam["px"]
    rel = tris.reshape(-1, 3) - cam["c"]
    sx = (rel @ cam["right"]) / px + w / 2
    sy = h / 2 - (rel @ cam["up"]) / px
    sz = rel @ cam["d"]
    sx, sy, sz = sx.reshape(-1, 3), sy.reshape(-1, 3), sz.reshape(-1, 3)
    fn = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-300)
    x0 = np.clip(np.ceil(sx.min(1) - 0.5), 0, w).astype(np.int64)
    x1 = np.clip(np.floor(sx.max(1) - 0.5), -1, w - 1).astype(np.int64)
    y0 = np.clip(np.ceil(sy.min(1) - 0.5), 0, h).astype(np.int64)
    y1 = np.clip(np.floor(sy.max(1) - 0.5), -1, h - 1).astype(np.int64)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    keep = np.nonzero((bw > 0) & (bh > 0))[0]
    N = w * h
    depth, tri_at = np.full(N, np.inf), np.full(N, -1, np.int64)
    cnt = (bw[keep] * bh[keep]).astype(np.int64)
    start = 0
    while start < len(keep):  # batches of about 4 million candidate pixels
        csum = np.cumsum(cnt[start:])
        stop = start + max(1, int(np.searchsorted(csum, 4_000_000)))
        k = keep[start:stop]
        c = cnt[start:stop]
        start = stop
        ti = np.repeat(k, c)
        off = np.arange(c.sum()) - np.repeat(np.cumsum(c) - c, c)
        X = x0[ti] + off % bw[ti]
        Y = y0[ti] + off // bw[ti]
        qx, qy = X + 0.5, Y + 0.5
        ax, ay, bx, by, cx, cy = sx[ti, 0], sy[ti, 0], sx[ti, 1], sy[ti, 1], sx[ti, 2], sy[ti, 2]
        area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        w0 = ((bx - qx) * (cy - qy) - (by - qy) * (cx - qx)) / np.where(area == 0, 1, area)
        w1 = ((cx - qx) * (ay - qy) - (cy - qy) * (ax - qx)) / np.where(area == 0, 1, area)
        w2 = 1 - w0 - w1
        ins = (area != 0) & (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        ti, X, Y, w0, w1, w2 = ti[ins], X[ins], Y[ins], w0[ins], w1[ins], w2[ins]
        z = w0 * sz[ti, 0] + w1 * sz[ti, 1] + w2 * sz[ti, 2]
        if cut is not None:
            P = w0[:, None] * tris[ti, 0] + w1[:, None] * tris[ti, 1] + w2[:, None] * tris[ti, 2]
            lo, hi = cut
            inside = np.all((P > np.array(lo)) & (P < np.array(hi)), axis=1)
            ti, X, Y, z = ti[~inside], X[~inside], Y[~inside], z[~inside]
        pix = Y * w + X
        order = np.lexsort((z, pix))
        pix, z, ti = pix[order], z[order], ti[order]
        first = np.ones(len(pix), bool)
        first[1:] = pix[1:] != pix[:-1]
        pix, z, ti = pix[first], z[first], ti[first]
        better = z < depth[pix]
        depth[pix[better]] = z[better]
        tri_at[pix[better]] = ti[better]
    hit = tri_at >= 0
    normal = np.zeros((N, 3))
    n = fn[tri_at[hit]]
    back = (n @ cam["d"]) > 0  # facing away: seen through a cut or an opening
    n[back] *= -1
    normal[hit] = n
    caps = np.zeros(N, bool)
    caps[np.nonzero(hit)[0][back]] = True
    return hit.reshape(h, w), depth.reshape(h, w), normal.reshape(h, w, 3), caps.reshape(h, w)


def _shade(np, cam, hit, depth, normal, caps):
    d, right, up = cam["d"], cam["right"], cam["up"]
    L = -0.45 * right + 0.6 * up - 0.66 * d
    L /= np.linalg.norm(L)
    lam = np.clip(normal @ L, 0, 1)
    fill = np.clip(normal @ -d, 0, 1)
    k = 0.34 + 0.56 * lam + 0.16 * fill
    col = np.where(caps[..., None], np.array(CAP), np.array(BASE)) * k[..., None]
    img = np.where(hit[..., None], np.clip(col, 0, 1), np.array(BG))
    # outlines: silhouettes, depth steps and creases
    edge = np.zeros(hit.shape, bool)
    px = cam["px"]
    dz = np.where(hit, depth, 1e300)
    for a, b in (((slice(None), slice(1, None)), (slice(None), slice(None, -1))),
                 ((slice(1, None), slice(None)), (slice(None, -1), slice(None)))):
        ha, hb = hit[a], hit[b]
        sil = ha != hb
        jump = ha & hb & (np.abs(dz[a] - dz[b]) > 6 * px)
        crease = ha & hb & (np.sum(normal[a] * normal[b], -1) < 0.80)
        e = sil | jump | crease
        edge[a] |= e
        edge[b] |= e
    img[edge] = img[edge] * 0.25 + np.array(INK) * 0.75
    return img


def _to_image(np, img):
    from PIL import Image
    im = Image.fromarray((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
    return im


def _compose(np, gfun, view, size=(W, H)):
    """Render the view (and its insets) with gfun(cam, cut) -> G-buffers; returns a PIL image."""
    from PIL import ImageDraw
    w, h = size
    cam = _camera(np, view, w * SS, h * SS)
    im = _to_image(np, _shade(np, cam, *gfun(cam, view.get("cut"))))
    im = im.resize((w, h), resample=_lanczos())
    for ins in view.get("insets", []):
        x, y, iw, ih = ins["rect"]
        icam = _camera(np, ins, iw * SS, ih * SS)
        sub = _to_image(np, _shade(np, icam, *gfun(icam, ins.get("cut")))).resize((iw, ih), resample=_lanczos())
        im.paste(sub, (x, y))
        dr = ImageDraw.Draw(im)
        dr.rectangle([x - 1, y - 1, x + iw, y + ih], outline=(90, 90, 90))
        if ins.get("label"):
            dr.text((x + 4, y + ih - 13), ins["label"], fill=(60, 60, 60))
    return im


def _lanczos():
    from PIL import Image
    return getattr(Image, "Resampling", Image).LANCZOS


def save_png(im, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.quantize(colors=96, dither=0).save(path, optimize=True)
    return path


def render_sdf(f, view):
    import numpy as np
    return _compose(np, lambda cam, cut: _gbuffer_sdf(np, f, cam, cut), view)


def render_mesh(tris, view):
    import numpy as np
    tris = np.asarray(tris, float) - np.array(view.get("offset", (0, 0, 0)), float)
    return _compose(np, lambda cam, cut: _gbuffer_mesh(np, tris, cam, cut), view)


def read_stl(paths):
    """Triangles from STL files (binary or ASCII) as an (n, 3, 3) array."""
    import numpy as np
    out = []
    for path in paths:
        data = open(path, "rb").read()
        n = struct.unpack("<I", data[80:84])[0] if len(data) >= 84 else -1
        if len(data) == 84 + 50 * n:
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                                count=n, offset=84)
            out.append(rec["v"].astype(float))
        else:
            nums = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)
            out.append(np.array(nums, float).reshape(-1, 3, 3))
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def write_stl(path, points, facets):
    """A binary STL from a point list and triangles of point indices (for harnesses that tessellate themselves)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"cad-acid-tests mesh".ljust(80, b" "))
        f.write(struct.pack("<I", len(facets)))
        for a, b, c in facets:
            f.write(struct.pack("<3f", 0, 0, 0))
            for i in (a, b, c):
                f.write(struct.pack("<3f", *points[i]))
            f.write(b"\0\0")
    return path


def render_completion(test, version, mesh_paths):
    """Render a program's finished output of a test (every stage passed) as docs/img/<test>-<program>.png."""
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
    except ImportError as e:
        print("render skipped (%s): the mesh is kept at %s" % (e, ", ".join(mesh_paths)), flush=True)
        return None
    tris = read_stl(mesh_paths)
    if not len(tris):
        print("render skipped: %s holds no triangles" % ", ".join(mesh_paths), flush=True)
        return None
    path = save_png(render_mesh(tris, VIEWS[test]), completion_png(test, version))
    print("render:", os.path.relpath(path, ROOT), flush=True)
    return path


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    render_completion(sys.argv[1], sys.argv[2], sys.argv[3:])
