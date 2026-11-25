//! The acid tests built with direct calls to SolidCraft's kernel: `solidcore-ops` (the operation
//! layer the app calls) and `solidcore` itself where ops has no call (the material census and the
//! closed-solid test). No app crate is linked: no sketches, no features, no commands.
//!
//! usage: acid-solidcore <input.json>
//!
//! The input (written by `solidcore/acid.py` from `expected/values.py`) names the test and gives
//! the probe points of each stage, acid-2's outline as Bezier segments and acid-9's fillet edge
//! picks. The binary builds the test stage by stage, as TESTS.md describes it, and after each
//! stage writes one JSON line to stdout with what it measured: the solids (the kernel's material
//! census), whether each body is a closed solid and passes the kernel's validity check, the
//! volumes, and where each probe point lies by the kernel's exact point classification. It
//! compares nothing with the expected values: acid.py does, against values.py.
//!
//! A stage that the kernel refuses ends the run with an `error` line. A stage that only exists as
//! an app feature (acid-11's gear and driven motion) is reported `not_run`.

use std::io::Write as _;
use std::time::Instant;

use serde_json::{json, Value};
use solidcore_ops::affine::{self, Matrix};
use solidcore_ops::blend::{blend, tangent_chain, Blend, Corners};
use solidcore_ops::boolean::{boolean, union_all};
use solidcore_ops::check::CheckLevel;
use solidcore_ops::offset::{draft, shell};
use solidcore_ops::split::{split_body, Tool};
use solidcore_ops::sweep::{extrude, loft, screw_sweep, Curve, DraftCorner};
use solidcore_ops::{
    make_box, make_cone, make_cylinder, make_sphere, make_torus, pick, session, Body, BooleanOp,
    Order, PointClass,
};
use solidcore_record::brep::Surface as RecordSurface;

type R<T> = std::result::Result<T, String>;

/// An ops error in words: its own, then the kernel's statement of why, when it left one.
fn err(what: &str, e: solidcore_ops::Error) -> String {
    match session::take_failure_detail() {
        Some(d) => format!("{what}: {e}: {d}"),
        None => format!("{what}: {e}"),
    }
}

macro_rules! ops {
    ($what:expr, $e:expr) => {{
        let _ = session::take_failure_detail();
        ($e).map_err(|e| err($what, e))?
    }};
}

const DEG: f64 = std::f64::consts::PI / 180.0;

// ------------------------------------------------------------------ the model: named bodies
struct Model {
    parts: Vec<(String, Body)>,
}

impl Model {
    fn new() -> Self {
        Model { parts: Vec::new() }
    }
    fn add(&mut self, name: &str, b: Body) {
        self.parts.push((name.to_owned(), b));
    }
    fn take(&mut self, name: &str) -> R<(usize, Body)> {
        let i = self
            .parts
            .iter()
            .position(|(n, _)| n == name)
            .ok_or_else(|| format!("no body called {name}"))?;
        Ok((i, self.parts.remove(i).1))
    }
    fn put(&mut self, at: usize, name: &str, b: Body) {
        self.parts.insert(at.min(self.parts.len()), (name.to_owned(), b));
    }
    fn get(&self, name: &str) -> R<&Body> {
        self.parts
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, b)| b)
            .ok_or_else(|| format!("no body called {name}"))
    }
    /// `name` op `tool`, the result in its place; an empty result is an error.
    fn boolean(&mut self, name: &str, tool: Body, op: BooleanOp, what: &str) -> R<()> {
        let (i, b) = self.take(name)?;
        let r = ops!(what, boolean(b, tool, op));
        let r = r.ok_or_else(|| format!("{what}: the result is empty"))?;
        self.put(i, name, r);
        Ok(())
    }
    fn cut(&mut self, name: &str, tool: Body, what: &str) -> R<()> {
        self.boolean(name, tool, BooleanOp::Subtract, what)
    }
    fn join(&mut self, name: &str, tool: Body, what: &str) -> R<()> {
        self.boolean(name, tool, BooleanOp::Union, what)
    }
    fn transform_all(&self, m: &Matrix, what: &str) -> R<()> {
        for (_, b) in &self.parts {
            ops!(what, b.transform(m));
        }
        Ok(())
    }
}

// ------------------------------------------------------------------ construction helpers
fn bx(corner: [f64; 3], size: [f64; 3]) -> R<Body> {
    Ok(ops!("box", make_box(corner, size)))
}

fn bx_center(c: [f64; 3], size: [f64; 3]) -> R<Body> {
    bx([c[0] - size[0] / 2.0, c[1] - size[1] / 2.0, c[2] - size[2] / 2.0], size)
}

/// A cylinder of radius `r` and height `h` from `base` along the unit direction `dir`.
fn cyl(base: [f64; 3], dir: [f64; 3], r: f64, h: f64) -> R<Body> {
    let c = ops!("cylinder", make_cylinder([0.0; 3], r, h));
    let turn = if dir == [0.0, 0.0, 1.0] {
        None
    } else if dir == [0.0, 0.0, -1.0] {
        Some(ops!("rotation", affine::rotation([0.0; 3], [1.0, 0.0, 0.0], 180.0 * DEG)))
    } else {
        // the turn taking +Z to dir: about Z x dir by the angle between them
        let ax = [-dir[1], dir[0], 0.0];
        let ang = dir[2].clamp(-1.0, 1.0).acos();
        Some(ops!("rotation", affine::rotation([0.0; 3], ax, ang)))
    };
    if let Some(m) = turn {
        ops!("rotate cylinder", c.transform(&m));
    }
    ops!("move cylinder", c.transform(&ops!("translation", affine::translation(base))));
    Ok(c)
}

fn zcyl(base: [f64; 3], r: f64, h: f64) -> R<Body> {
    Ok(ops!("cylinder", make_cylinder(base, r, h)))
}

fn polar(r: f64, deg: f64, z: f64) -> [f64; 3] {
    [r * (deg * DEG).cos(), r * (deg * DEG).sin(), z]
}

fn mv(by: [f64; 3]) -> R<Matrix> {
    Ok(ops!("translation", affine::translation(by)))
}

/// The body's own lists as an [`Order`]: record index i is native index i.
fn native_order(b: &Body) -> Order {
    let k = b.kernel();
    Order {
        faces: k.face_list().iter().copied().collect(),
        edges: k.edge_list().iter().copied().collect(),
        vertices: k.vertex_list().iter().copied().collect(),
        split_edges: 0,
    }
}

/// The faces the points lie on, by the kernel's classification.
fn faces_at(b: &Body, pts: &[[f64; 3]]) -> R<Vec<usize>> {
    let o = native_order(b);
    let mut out = Vec::new();
    for p in pts {
        let mut found = None;
        for reach in [1e-6, 1e-5, 1e-4] {
            found = ops!("face pick", b.face_at(&o, *p, reach));
            if found.is_some() {
                break;
            }
        }
        if found.is_none() {
            found = seam_face(b, &o, *p)?;
        }
        out.push(found.ok_or_else(|| format!("no face at {p:?}"))?);
    }
    Ok(out)
}

/// The face whose seam the point lies on: a point on a cylinder's or a cone's seam is on an
/// edge, which the kernel's face pick leaves to no face; the seam edge has one face on both sides.
fn seam_face(b: &Body, o: &Order, p: [f64; 3]) -> R<Option<usize>> {
    let Ok(Ok(e)) = pick::edges(b, &[p], 1e-6) else {
        return Ok(None);
    };
    let Some(&e) = e.first() else { return Ok(None) };
    let k = b.kernel();
    let Some(&eid) = k.edge_list().get(e) else { return Ok(None) };
    let faces = solidcore::topology::edge::faces(&k, eid, None).map_err(|e| format!("edge faces: {e}"))?;
    Ok(match faces.as_slice() {
        [f] => o.faces.iter().position(|x| x == f),
        _ => None,
    })
}

/// The edges nearest the points, then (with `chain`) every edge tangent-continuing them.
fn edges_at(b: &Body, pts: &[[f64; 3]], chain: bool) -> R<Vec<usize>> {
    let picked = ops!("edge pick", pick::edges(b, pts, 1e-3));
    let mut edges = picked.map_err(|m| match m {
        pick::Miss::None(i) => format!("no edge within 0.001 of {:?}", pts[i]),
        pick::Miss::Ambiguous(i) => format!("two edges equally near {:?}", pts[i]),
    })?;
    edges.dedup();
    let mut uniq: Vec<usize> = Vec::new();
    for e in edges {
        if !uniq.contains(&e) {
            uniq.push(e);
        }
    }
    if !chain {
        return Ok(uniq);
    }
    let c = ops!("tangent chain", tangent_chain(b, None, &uniq, &[], 1.0 * DEG));
    Ok(c.members)
}

fn round(m: &Model, name: &str, pts: &[[f64; 3]], r: f64, chain: bool) -> R<()> {
    let b = m.get(name)?;
    let edges = edges_at(b, pts, chain)?;
    ops!("fillet", blend(b, Blend::Round, &edges, &[r], &[], Corners::Filled));
    Ok(())
}

fn chamfer(m: &Model, name: &str, pts: &[[f64; 3]], d: f64) -> R<()> {
    let b = m.get(name)?;
    let edges = edges_at(b, pts, false)?;
    ops!("chamfer", blend(b, Blend::Chamfer, &edges, &[d], &[], Corners::Filled));
    Ok(())
}

/// Shell inward by `t`, the faces at `open` removed.
fn shell_in(m: &Model, name: &str, open: &[[f64; 3]], t: f64) -> R<()> {
    let b = m.get(name)?;
    let faces = faces_at(b, open)?;
    ops!("shell", shell(b, &native_order(b), &faces, -t));
    Ok(())
}

/// Draft the faces at `pts` by `deg` (positive leans in going along +Z) about the plane through
/// `origin` with normal +Z.
fn draft_z(m: &Model, name: &str, pts: &[[f64; 3]], origin: [f64; 3], deg: f64) -> R<()> {
    let b = m.get(name)?;
    let faces = faces_at(b, pts)?;
    let z = [0.0, 0.0, 1.0];
    ops!("draft", draft(b, &native_order(b), &faces, origin, z, z, deg * DEG));
    Ok(())
}

fn plane_tool(origin: [f64; 3], u: [f64; 3], v: [f64; 3], half: f64) -> Tool {
    Tool {
        surface: RecordSurface::Plane { origin, u_axis: u, v_axis: v },
        domain: [[-half, half], [-half, half]],
    }
}

/// Split body `name` by `tool`, keeping both: the back part keeps the name, the front is
/// `name (1)`, after it.
fn split(m: &mut Model, name: &str, tool: &Tool) -> R<()> {
    let (i, b) = m.take(name)?;
    let r = {
        let _ = session::take_failure_detail();
        split_body(&b, tool).map_err(|e| err("split", e))
    };
    match r {
        Ok(Some((back, front))) => {
            m.put(i, name, back);
            m.put(i + 1, &format!("{name} (1)"), front);
            Ok(())
        }
        Ok(None) => {
            m.put(i, name, b);
            Err("split: the plane leaves a side empty or only nicks the body".to_owned())
        }
        Err(e) => {
            m.put(i, name, b);
            Err(e)
        }
    }
}

/// A copy of body `name` mirrored in the plane, added as `name (1)`.
fn mirror(m: &mut Model, name: &str, origin: [f64; 3], normal: [f64; 3]) -> R<()> {
    let b = m.get(name)?;
    let c = ops!("copy", b.copy());
    ops!("mirror", c.transform(&ops!("reflection", affine::reflection(origin, normal))));
    let at = m.parts.iter().position(|(n, _)| n == name).unwrap_or(0) + 1;
    m.put(at, &format!("{name} (1)"), c);
    Ok(())
}

// ------------------------------------------------------------------ the inputs acid.py passes
struct Input {
    test: String,
    probes: Vec<Vec<[f64; 3]>>,
    spline: Vec<[[f64; 2]; 4]>,
    spline_h: Vec<f64>,
    tower_edges: Vec<[f64; 3]>,
}

fn p3(v: &Value) -> [f64; 3] {
    let f = |i: usize| v.get(i).and_then(Value::as_f64).unwrap_or(f64::NAN);
    [f(0), f(1), f(2)]
}

fn read_input(path: &str) -> R<Input> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
    let v: Value = serde_json::from_str(&text).map_err(|e| format!("{path}: {e}"))?;
    let arr = |k: &str| v.get(k).and_then(Value::as_array).cloned().unwrap_or_default();
    let probes = arr("probes")
        .iter()
        .map(|s| s.as_array().map(|a| a.iter().map(p3).collect()).unwrap_or_default())
        .collect();
    let spline = arr("spline")
        .iter()
        .map(|seg| {
            let mut out = [[0.0; 2]; 4];
            for (j, o) in out.iter_mut().enumerate() {
                let p = &seg[j];
                *o = [p[0].as_f64().unwrap_or(f64::NAN), p[1].as_f64().unwrap_or(f64::NAN)];
            }
            out
        })
        .collect();
    Ok(Input {
        test: v.get("test").and_then(Value::as_str).unwrap_or("").to_owned(),
        probes,
        spline,
        spline_h: arr("spline_h").iter().filter_map(Value::as_f64).collect(),
        tower_edges: arr("tower_edges").iter().map(p3).collect(),
    })
}

// ------------------------------------------------------------------ the tests
type Stage<'a> = Box<dyn Fn(&mut Model) -> R<Option<String>> + 'a>;

/// A stage, its closure's argument typed.
fn st<'a>(f: impl Fn(&mut Model) -> R<Option<String>> + 'a) -> Stage<'a> {
    Box::new(f)
}

fn done(_: ()) -> Option<String> {
    None
}

fn acid1<'a>() -> Vec<Stage<'a>> {
    let bxp = (30.0f64 * 30.0 - 0.95001 * 0.95001).sqrt();
    vec![
        st(|m| {
            m.add("Part", ops!("torus", make_torus([0.0; 3], 30.0, 8.0)));
            m.cut("Part", bx([-100.0, -100.0, -100.0], [200.0, 200.0, 100.0])?, "half torus").map(done)
        }),
        st(move |m| m.join("Part", zcyl([bxp, 0.95001, -1.9], 0.95, 1.9)?, "boss").map(done)),
        st(|m| m.cut("Part", zcyl([0.0, 38.0, -1.0], 0.7, 4.0)?, "notch").map(done)),
        st(|m| round(m, "Part", &[[-38.0, 0.0, 0.0], [0.0, 37.3, 0.0], [-22.0, 0.0, 0.0]], 0.8, true).map(done)),
        st(move |m| shell_in(m, "Part", &[[bxp, 0.95001, -1.9]], 0.6).map(done)),
        st(|m| {
            for k in 0..12 {
                m.cut("Part", zcyl(polar(30.0, 15.0 + 30.0 * f64::from(k), -20.0), 0.5, 40.0)?, "hole")?;
            }
            Ok(None)
        }),
        st(|m| split(m, "Part", &plane_tool([0.0, 0.0, 4.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], 500.0)).map(done)),
        st(|m| m.transform_all(&mv([10000.0, 0.0, 0.0])?, "move").map(done)),
    ]
}

/// acid-2's outline: the C2 cubic B-spline with simple knots at the chord lengths, from its
/// Bezier segments; the segments themselves (triple knots) if they do not join C2 to 1e-9.
fn outline(segs: &[[[f64; 2]; 4]], h: &[f64]) -> R<Curve> {
    let n = segs.len();
    if n < 3 || h.len() != n {
        return Err("acid-2: no outline in the input".to_owned());
    }
    let hh = |i: isize| if i < 0 || i as usize >= n { 0.0 } else { h[i as usize] };
    let mut pts: Vec<Option<[f64; 2]>> = vec![None; n + 3];
    pts[0] = Some(segs[0][0]);
    pts[n + 2] = Some(segs[n - 1][3]);
    let mut c2 = true;
    for i in 0..n {
        let ii = i as isize;
        let delta = hh(ii - 1) + hh(ii) + hh(ii + 1);
        let (t1, t2) = (hh(ii - 1) / delta, (hh(ii - 1) + hh(ii)) / delta);
        let (b1, b2) = (segs[i][1], segs[i][2]);
        let d = [(b2[0] - b1[0]) / (t2 - t1), (b2[1] - b1[1]) / (t2 - t1)];
        let pa = [b1[0] - t1 * d[0], b1[1] - t1 * d[1]];
        let pb = [pa[0] + d[0], pa[1] + d[1]];
        for (k, p) in [(i + 1, pa), (i + 2, pb)] {
            match pts[k] {
                Some(q) if ((q[0] - p[0]).powi(2) + (q[1] - p[1]).powi(2)).sqrt() > 1e-9 => c2 = false,
                Some(_) => {}
                None => pts[k] = Some(p),
            }
        }
    }
    let mut cum = vec![0.0];
    for x in h {
        cum.push(cum.last().copied().unwrap_or(0.0) + x);
    }
    let total = *cum.last().unwrap_or(&1.0);
    if c2 {
        let mut knots = vec![0.0; 4];
        knots.extend(cum[1..n].iter().copied());
        knots.extend([total; 4]);
        let points = pts.iter().map(|p| {
            let p = p.unwrap_or([f64::NAN; 2]);
            [p[0], p[1], 0.0, 1.0]
        });
        return Ok(Curve::Nurbs { degree: 3, points: points.collect(), knots });
    }
    let mut knots = vec![0.0; 4];
    let mut points = vec![[segs[0][0][0], segs[0][0][1], 0.0, 1.0]];
    for (i, s) in segs.iter().enumerate() {
        for p in &s[1..] {
            points.push([p[0], p[1], 0.0, 1.0]);
        }
        let k = if i + 1 == n { 4 } else { 3 };
        knots.extend(std::iter::repeat_n(cum[i + 1], k));
    }
    Ok(Curve::Nurbs { degree: 3, points, knots })
}

fn acid2<'a>(inp: &'a Input) -> Vec<Stage<'a>> {
    let mut bosses = Vec::new();
    for x in [-30.0, 30.0] {
        for y in [-12.0, 12.0] {
            bosses.push([x, y]);
        }
    }
    let b2 = bosses.clone();
    // TESTS.md picks the top loop and the side at x = 60, where the outline starts: the kernel
    // makes that point a vertex on the side's seam, so a pick there is ambiguous (two edges) or
    // on no one face. The outline's point midway along its first segment picks the same edge
    // and face.
    let mid = inp.spline.first().map_or([60.0, 0.0], |s| {
        let f = |k: usize| (s[0][k] + 3.0 * s[1][k] + 3.0 * s[2][k] + s[3][k]) / 8.0;
        [f(0), f(1)]
    });
    const SEAM: &str = "picked midway along the outline's first segment: (60, 0) is the kernel's seam vertex";
    vec![
        st(move |m| {
            let c = outline(&inp.spline, &inp.spline_h)?;
            let b = ops!("extrude", extrude(&[c], [0.0, 0.0, 1.0], 25.0, 0.0, DraftCorner::Curvature, true));
            m.add("Part", b);
            Ok(None)
        }),
        st(move |m| round(m, "Part", &[[mid[0], mid[1], 25.0]], 3.0, true).map(|()| Some(SEAM.to_owned()))),
        st(move |m| {
            for [x, y] in &bosses {
                m.join("Part", zcyl([*x, *y, 25.0], 3.0, 10.0)?, "boss")?;
            }
            Ok(None)
        }),
        st(move |m| draft_z(m, "Part", &[[mid[0], mid[1], 10.0]], [0.0; 3], -3.0).map(|()| Some(SEAM.to_owned()))),
        st(|m| shell_in(m, "Part", &[[0.0, 0.0, 0.0]], 1.5).map(done)),
        st(move |m| {
            for [x, y] in &b2 {
                m.cut("Part", zcyl([*x, *y, -5.0], 2.0, 50.0)?, "hole")?;
            }
            Ok(None)
        }),
        st(|m| mirror(m, "Part", [80.0, 0.0, 0.0], [1.0, 0.0, 0.0]).map(done)),
        st(|m| {
            m.join("Part", bx([55.0, -5.0, 0.0], [50.0, 10.0, 5.0])?, "bar")?;
            let (_, other) = m.take("Part (1)")?;
            m.join("Part", other, "join mirrored body").map(done)
        }),
    ]
}

/// The M6 x 1 internal thread's groove (ISO basic profile: P/8 wide at the major diameter,
/// flanks at 60 degrees), run on past the minor diameter into the hole, swept 15 turns up
/// from z = 15 about the hole's axis through (40, 30).
fn m6_thread() -> R<Body> {
    let (c, y) = (40.0, 30.0);
    let tan30 = (30.0 * DEG).tan();
    let (r_in, r_out) = (2.3, 3.0);
    let w_out = 1.0 / 16.0; // half of P/8
    let w_in = w_out + (r_out - r_in) * tan30;
    let z0 = 15.0 + w_in;
    let p = |r: f64, dz: f64| [c + r, y, z0 + dz];
    let pts = [p(r_in, -w_in), p(r_out, -w_out), p(r_out, w_out), p(r_in, w_in)];
    let profile: Vec<Curve> = (0..4)
        .map(|i| Curve::Line { start: pts[i], end: pts[(i + 1) % 4] })
        .collect();
    Ok(ops!("thread (screw sweep)", screw_sweep(&profile, [c, y, 0.0], [0.0, 0.0, 1.0], 1.0, 15.0, None, 1e-5)))
}

fn acid3<'a>() -> Vec<Stage<'a>> {
    let top = [
        [40.0, 0.0, 30.0], [80.0, 30.0, 30.0], [40.0, 60.0, 30.0], [0.0, 30.0, 30.0],
        [1.50001, 1.5, 30.0], [0.000005, 0.0, 30.0],
    ];
    vec![
        st(|m| {
            m.add("Part", bx([0.0; 3], [80.0, 60.0, 30.0])?);
            Ok(None)
        }),
        st(|m| {
            for i in 0..3 {
                for j in 0..2 {
                    let c = [20.0 + 20.0 * f64::from(i), 20.0 + 20.0 * f64::from(j), 31.0];
                    m.join("Part", bx_center(c, [3.0, 3.0, 2.0])?, "pad")?;
                }
            }
            Ok(None)
        }),
        st(|m| {
            m.cut("Part", zcyl([40.0, 30.0, 10.0], 4.917 / 2.0, 20.0)?, "hole")?;
            m.cut("Part", m6_thread()?, "thread")?;
            Ok(Some("the modelled thread is the ISO M6x1 basic groove as a screw sweep (solidcore-ops screw_sweep), cut from the hole".to_owned()))
        }),
        st(|m| {
            for k in 0..8 {
                let a = 45.0 * f64::from(k) * DEG;
                m.cut("Part", zcyl([40.0 + 12.0 * a.cos(), 30.0 + 12.0 * a.sin(), 20.0], 1.25, 12.0)?, "hole")?;
            }
            Ok(None)
        }),
        st(|m| m.cut("Part", zcyl([1.50001, 0.0, 26.0], 1.5, 6.0)?, "notch").map(done)),
        st(|m| shell_in(m, "Part", &[], 1.2).map(done)),
        st(move |m| round(m, "Part", &top, 0.5, true).map(done)),
    ]
}

fn acid4<'a>() -> Vec<Stage<'a>> {
    let x = [1.0, 0.0, 0.0];
    let y = [0.0, 1.0, 0.0];
    let z = [0.0, 0.0, 1.0];
    let mut mids = Vec::new();
    for a in [-20.0, 20.0] {
        for b in [-20.0, 20.0] {
            mids.push([a, b, 0.0]);
            mids.push([a, 0.0, b]);
            mids.push([0.0, a, b]);
        }
    }
    vec![
        st(|m| {
            m.add("Part", bx([-20.0; 3], [40.0; 3])?);
            Ok(None)
        }),
        st(move |m| {
            m.cut("Part", cyl([-21.0, 0.0, 0.0], x, 9.0, 42.0)?, "X hole")?;
            m.cut("Part", cyl([0.0, -21.0, 0.0], y, 9.0, 42.0)?, "Y hole")?;
            m.cut("Part", cyl([0.0, 0.0, -21.0], z, 9.0, 42.0)?, "Z hole").map(done)
        }),
        st(move |m| {
            for yy in [-10.0, 10.0] {
                m.cut("Part", cyl([-21.0, yy, 0.0], x, 4.0, 42.0)?, "X hole")?;
            }
            for xx in [-10.0, 10.0] {
                m.cut("Part", cyl([xx, -21.0, 1e-5], y, 4.0, 42.0)?, "Y hole")?;
            }
            Ok(None)
        }),
        st(|m| {
            m.add("Cyl", zcyl([26.0, 0.0, -30.0], 5.0, 60.0)?);
            m.join("Cyl", zcyl([35.99999, 0.0, -30.0], 5.0, 60.0)?, "second cylinder").map(done)
        }),
        st(|m| m.join("Cyl", ops!("sphere", make_sphere([26.0, 0.0, 34.99999], 5.0)), "sphere").map(done)),
        st(move |m| round(m, "Part", &mids, 2.0, false).map(done)),
    ]
}

fn acid5<'a>() -> Vec<Stage<'a>> {
    vec![
        st(|m| {
            m.add("Part", bx([1000.0; 3], [30.0, 20.0, 10.0])?);
            Ok(None)
        }),
        st(|m| {
            let mut pts = Vec::new();
            for x in [1000.0, 1030.0] {
                for y in [1000.0, 1020.0] {
                    pts.push([x, y, 1005.0]);
                }
            }
            round(m, "Part", &pts, 1.3, true).map(done)
        }),
        st(|m| round(m, "Part", &[[1015.0, 1000.0, 1010.0]], 1.29, true).map(done)),
        st(|m| chamfer(m, "Part", &[[1015.0, 1000.0, 1000.0]], 0.00002).map(done)),
        st(|m| round(m, "Part", &[[1000.0, 1010.0, 1000.0]], 0.000025, false).map(done)),
        st(|m| shell_in(m, "Part", &[[1015.0, 1010.0, 1000.0]], 0.4).map(done)),
    ]
}

fn acid6<'a>() -> Vec<Stage<'a>> {
    let z0 = 499.99975;
    vec![
        st(move |m| {
            m.add("Part", ops!("sphere", make_sphere([0.0; 3], 500.0)));
            m.cut("Part", bx([-1000.0, -1000.0, z0], [2000.0, 2000.0, 1000.0])?, "flat").map(done)
        }),
        st(move |m| m.join("Part", zcyl([0.0, 0.0, z0], 0.06, 0.12)?, "pin").map(done)),
        st(|m| {
            m.add("Ring", ops!("torus", make_torus([0.0; 3], 500.0, 0.5)));
            m.cut("Ring", bx([-1000.0, -1000.0, -10.0], [2000.0, 2000.0, 10.0])?, "half ring")?;
            ops!("move", m.get("Ring")?.transform(&mv([0.0, 0.0, 600.0])?));
            Ok(None)
        }),
        st(|m| shell_in(m, "Ring", &[], 0.06).map(done)),
        st(|m| {
            m.add("Needle", zcyl([0.0, 0.0, -1600.0], 0.5, 1000.0)?);
            draft_z(m, "Needle", &[[0.5, 0.0, -1100.0]], [0.0, 0.0, -1600.0], 0.02).map(done)
        }),
        st(|m| {
            m.join("Part", zcyl([0.0, 0.0, -600.0], 0.4, 200.0)?, "rod")?;
            let (_, needle) = m.take("Needle")?;
            m.join("Part", needle, "needle").map(done)
        }),
    ]
}

fn acid7<'a>() -> Vec<Stage<'a>> {
    let taper = (60.0f64 / 200.0).atan() / DEG;
    let r_top = 100.0 - 200.0 * (18.0 * DEG).tan();
    let mut rim = Vec::new();
    for k in 0..6 {
        rim.push(polar(r_top, 30.0 + 60.0 * f64::from(k), 200.0));
    }
    for k in 0..6 {
        rim.push(polar(27.0, 60.0 * f64::from(k), 200.0));
    }
    vec![
        st(|m| {
            m.add("Part", ops!("cone", make_cone([0.0; 3], 100.0, 40.0, 200.0)));
            Ok(None)
        }),
        st(|m| {
            for k in 0..6 {
                m.cut("Part", zcyl(polar(32.0, 60.0 * f64::from(k), 150.0), 5.0, 51.0)?, "hole")?;
            }
            Ok(None)
        }),
        // ops' draft of a cone turns its generators by the angle, from the cone's own taper
        st(move |m| draft_z(m, "Part", &[[70.0, 0.0, 100.0]], [0.0; 3], 18.0 - taper).map(done)),
        st(move |m| round(m, "Part", &rim, 0.5, true).map(done)),
        st(|m| m.cut("Part", zcyl([0.0, 0.0, -1.0], 60.0, 81.0)?, "pocket").map(done)),
        st(|m| shell_in(m, "Part", &[[80.0, 0.0, 0.0]], 2.0).map(done)),
    ]
}

/// A ring of 24 radial holes at height z, as one tool (the pattern's instances, unioned).
fn hole_ring(z: f64) -> R<Body> {
    let mut tools = Vec::new();
    for k in 0..24 {
        let a = 15.0 * f64::from(k) * DEG;
        let dir = [-a.cos(), -a.sin(), 0.0];
        tools.push(cyl([50.0 * a.cos(), 50.0 * a.sin(), z], dir, 3.0, 20.0)?);
    }
    Ok(ops!("pattern instances", union_all(tools)))
}

fn acid8<'a>() -> Vec<Stage<'a>> {
    vec![
        st(|m| {
            m.add("Part", zcyl([0.0; 3], 50.0, 120.0)?);
            Ok(None)
        }),
        st(|m| m.cut("Part", hole_ring(20.0)?, "circular pattern").map(done)),
        st(|m| {
            for k in 1..5 {
                m.cut("Part", hole_ring(20.0 + 20.0 * f64::from(k))?, "linear pattern")?;
            }
            Ok(None)
        }),
        st(|m| {
            for k in 0..7 {
                m.join("Part", zcyl(polar(30.0, 15.0 * f64::from(k), 120.0), 2.0, 3.0)?, "boss")?;
            }
            Ok(None)
        }),
        st(|m| shell_in(m, "Part", &[], 2.0).map(done)),
        st(|m| split(m, "Part", &plane_tool([0.0, 0.0, 61.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], 500.0)).map(done)),
        st(|m| {
            // the upper of the two bodies the split made
            let mut best: Option<(f64, String)> = None;
            for (n, b) in &m.parts {
                let (_, hi) = ops!("bounds", b.bounds());
                if best.as_ref().is_none_or(|(z, _)| hi[2] > *z) {
                    best = Some((hi[2], n.clone()));
                }
            }
            let name = best.map(|(_, n)| n).ok_or("no bodies")?;
            mirror(m, &name, [0.0, 0.0, 140.0], [0.0, 0.0, 1.0]).map(done)
        }),
    ]
}

fn tower_section(z: f64) -> Vec<[f64; 3]> {
    let t = z / 100.0;
    let ra = 20.0 * 2f64.sqrt();
    (0..4)
        .map(|i| {
            let a = (45.0 + 90.0 * f64::from(i)) * DEG;
            let b = (90.0 + 90.0 * f64::from(i)) * DEG;
            [(1.0 - t) * ra * a.cos() + t * ra * b.cos(), (1.0 - t) * ra * a.sin() + t * ra * b.sin(), z]
        })
        .collect()
}

fn square(z: f64) -> Vec<Curve> {
    let p = tower_section(z);
    (0..4).map(|i| Curve::Line { start: p[i], end: p[(i + 1) % 4] }).collect()
}

fn acid9<'a>(inp: &'a Input) -> Vec<Stage<'a>> {
    vec![
        st(|m| {
            let b = ops!("loft", loft(&[square(0.0), square(100.0)], 1, true));
            m.add("Part", b);
            Ok(None)
        }),
        st(|m| m.cut("Part", zcyl([0.0, 0.0, -1.0], 36.94 / 2.0, 102.0)?, "bore").map(done)),
        st(|m| {
            m.boolean("Part", ops!("sphere", make_sphere([0.0, 0.0, 50.0], 55.0)), BooleanOp::Intersect, "sphere").map(done)
        }),
        st(move |m| round(m, "Part", &inp.tower_edges, 1.0, true).map(done)),
        st(|m| shell_in(m, "Part", &[[0.0, 22.0, 100.0]], 1.0).map(done)),
        st(|m| {
            let s = 0.5f64.sqrt();
            split(m, "Part", &plane_tool([0.0; 3], [s, s, 0.0], [0.0, 0.0, 1.0], 500.0)).map(done)
        }),
    ]
}

fn acid10<'a>() -> Vec<Stage<'a>> {
    vec![
        st(|m| {
            m.add("Part", bx([0.0; 3], [50.0; 3])?);
            Ok(None)
        }),
        st(|m| m.join("Part", cyl([0.0, 25.0, 59.99998], [1.0, 0.0, 0.0], 10.0, 50.0)?, "cylinder").map(done)),
        st(|m| m.join("Part", ops!("sphere", make_sphere([64.99998, 25.0, 25.0], 15.0)), "sphere").map(done)),
        st(|m| m.cut("Part", bx([20.0, -1.0, -1.0], [10.0, 1.00002, 52.0])?, "groove").map(done)),
        st(|m| {
            let mut pts = Vec::new();
            for x in [0.0, 50.0] {
                for y in [0.0, 50.0] {
                    pts.push([x, y, 25.0]);
                }
            }
            round(m, "Part", &pts, 5.0, false).map(done)
        }),
        st(|m| m.transform_all(&mv([1e6, 1e6, 0.0])?, "move").map(done)),
    ]
}

/// Runs the first `n` stages of a test on a model of its own and returns its body `Part`.
fn part_of(stages: Vec<Stage<'_>>, n: usize) -> R<Body> {
    let mut m = Model::new();
    for s in stages.iter().take(n) {
        s(&mut m)?;
    }
    Ok(m.take("Part")?.1)
}

fn acid11<'a>(inp: &'a Input) -> Vec<Stage<'a>> {
    vec![
        st(|m| {
            m.add("Base", bx([-80.0, -50.0, 0.0], [160.0, 100.0, 10.0])?);
            m.join("Base", zcyl([-40.0, 0.0, 10.0], 3.0, 2.0)?, "pin")?;
            m.join("Base", zcyl([13.5, 0.0, 10.0], 3.0, 2.0)?, "pin").map(done)
        }),
        st(|m| {
            let ring = part_of(acid1(), 3)?;
            ops!("move", ring.transform(&mv([-40.0, 0.0, 30.0])?));
            m.add("Ring", ring);
            Ok(Some("placed by its move; the revolute joint is the app's assembly, not run".to_owned()))
        }),
        st(move |m| {
            let cam = part_of(acid2(inp), 3)?;
            ops!("scale", cam.transform(&ops!("scaling", affine::scaling([0.0; 3], 0.25))));
            m.add("Cam", cam);
            m.cut("Cam", zcyl([0.0, 0.0, -10.0], 1.0, 50.0)?, "axle hole")?;
            ops!("move", m.get("Cam")?.transform(&mv([13.5, 0.0, 30.0])?));
            Ok(Some("placed by its move; the revolute joint is the app's assembly, not run".to_owned()))
        }),
    ]
}

/// acid-11 stage 6 at the kernel: each step's placement worked out here (ring a degrees about
/// its axis, cam -3a about its own), every pair checked with the kernel's contact test.
fn acid11_interference(m: &Model) -> R<Vec<String>> {
    let mut bad = Vec::new();
    let z = [0.0, 0.0, 1.0];
    for k in 0..36 {
        let a = f64::from(10 * k);
        let ring = ops!("copy", m.get("Ring")?.copy());
        ops!("turn", ring.transform(&ops!("rotation", affine::rotation([-40.0, 0.0, 0.0], z, a * DEG))));
        let cam = ops!("copy", m.get("Cam")?.copy());
        ops!("turn", cam.transform(&ops!("rotation", affine::rotation([13.5, 0.0, 0.0], z, -3.0 * a * DEG))));
        let base = m.get("Base")?;
        for (na, ba, nb, bb) in [("Base", base, "Ring", &ring), ("Base", base, "Cam", &cam), ("Ring", &ring, "Cam", &cam)] {
            let what = format!("contact of {na} and {nb} at {a} degrees");
            if ops!(&what, ba.meets(bb, 1e-6)) {
                bad.push(format!("at {a} degrees {na} meets {nb}"));
            }
        }
        if !bad.is_empty() {
            break;
        }
    }
    Ok(bad)
}

// ------------------------------------------------------------------ measuring a stage
fn measure(m: &Model, probes: &[[f64; 3]]) -> Value {
    let mut bodies = 0usize;
    let mut closed = true;
    let mut valid = true;
    let mut volume = 0.0;
    let mut vols = Vec::new();
    let mut notes = Vec::new();
    for (name, b) in &m.parts {
        let (solids, is_closed) = {
            let k = b.kernel();
            let census = solidcore::query::api_queries::brep_material_census(&k);
            let c = solidcore::query::api_queries::brep_is_closed_touching_solids(&k);
            (census.map(|(s, _)| s), c)
        };
        match solids {
            Ok(s) => bodies += s,
            Err(e) => {
                notes.push(format!("{name}: census refused: {e}"));
                bodies += 1;
            }
        }
        match is_closed {
            Ok(true) => {}
            Ok(false) => {
                closed = false;
                notes.push(format!("{name}: not a closed solid"));
            }
            Err(e) => {
                closed = false;
                notes.push(format!("{name}: closed test refused: {e}"));
            }
        }
        match b.check(CheckLevel::Geometry) {
            Ok(r) if r.is_valid() => {}
            Ok(r) => {
                valid = false;
                let first = r.text.lines().next().unwrap_or("").to_owned();
                notes.push(format!("{name}: kernel check: {} failed tests ({first})", r.failures));
            }
            Err(e) => {
                notes.push(format!("{name}: kernel check could not run: {e}"));
            }
        }
        match b.mass_properties(1e-8) {
            Ok(p) => {
                volume += p.volume;
                vols.push(p.volume);
            }
            Err(e) => {
                notes.push(format!("{name}: volume refused: {e}"));
                vols.push(f64::NAN);
            }
        }
    }
    let mut classes = Vec::new();
    for p in probes {
        let mut class = "outside".to_owned();
        for (name, b) in &m.parts {
            match b.classify_point(*p) {
                Ok(PointClass::Inside) => class = "inside".to_owned(),
                Ok(PointClass::OnBoundary) if class == "outside" => class = "on".to_owned(),
                Ok(_) => {}
                Err(e) => class = format!("refused ({name}: {e})"),
            }
            if class == "inside" {
                break;
            }
        }
        classes.push(class);
    }
    json!({
        "bodies": bodies,
        "closed": closed,
        "check_valid": valid,
        "volume_mm3": volume,
        "solid_volumes": vols,
        "names": m.parts.iter().map(|(n, _)| n.clone()).collect::<Vec<_>>(),
        "probes": classes,
        "notes": notes,
    })
}

// ------------------------------------------------------------------ expected/kernel_ops.py, op for op
/// One neutral op of the shared spec, as one solidcore-ops call (a cylinder off +Z is the +Z
/// cylinder turned onto its axis and moved to its base: make_cylinder takes no axis).
fn spec_op(m: &mut Model, o: &Value) -> R<()> {
    let s = |k: &str| o.get(k).and_then(Value::as_str).map(str::to_owned).ok_or_else(|| format!("op lacks {k}: {o}"));
    let f = |k: &str| o.get(k).and_then(Value::as_f64).ok_or_else(|| format!("op lacks {k}: {o}"));
    let v = |k: &str| -> R<[f64; 3]> {
        let a = o.get(k).and_then(Value::as_array).ok_or_else(|| format!("op lacks {k}: {o}"))?;
        let x = |i: usize| a.get(i).and_then(Value::as_f64).ok_or_else(|| format!("bad {k}: {o}"));
        Ok([x(0)?, x(1)?, x(2)?])
    };
    let names = |m: &Model| -> R<Vec<String>> {
        match o.get("bodies") {
            Some(Value::String(x)) if x == "*" => Ok(m.parts.iter().map(|(n, _)| n.clone()).collect()),
            Some(Value::Array(a)) => Ok(a.iter().filter_map(Value::as_str).map(str::to_owned).collect()),
            _ => Err(format!("op lacks bodies: {o}")),
        }
    };
    let moved = |m: &Model, mat: Matrix, what: &str| -> R<()> {
        for n in names(m)? {
            ops!(what, m.get(&n)?.transform(&mat));
        }
        Ok(())
    };
    let op = s("op")?;
    match op.as_str() {
        "box" => m.add(&s("out")?, ops!("box", make_box(v("corner")?, v("size")?))),
        "cylinder" => {
            let (base, axis) = (v("base")?, v("axis")?);
            let b = if axis == [0.0, 0.0, 1.0] {
                ops!("cylinder", make_cylinder(base, f("r")?, f("h")?))
            } else {
                cyl(base, axis, f("r")?, f("h")?)?
            };
            m.add(&s("out")?, b);
        }
        "cone" => {
            if v("axis")? != [0.0, 0.0, 1.0] {
                return Err("cone: only +Z".to_owned());
            }
            m.add(&s("out")?, ops!("cone", make_cone(v("base")?, f("r1")?, f("r2")?, f("h")?)));
        }
        "sphere" => m.add(&s("out")?, ops!("sphere", make_sphere(v("c")?, f("r")?))),
        "torus" => {
            if v("axis")? != [0.0, 0.0, 1.0] {
                return Err("torus: only +Z".to_owned());
            }
            m.add(&s("out")?, ops!("torus", make_torus(v("c")?, f("R")?, f("r")?)));
        }
        "copy" => {
            let c = ops!("copy", m.get(&s("src")?)?.copy());
            m.add(&s("out")?, c);
        }
        "rename" => {
            let (i, b) = m.take(&s("src")?)?;
            m.put(i, &s("out")?, b);
        }
        "translate" => moved(m, ops!("translation", affine::translation(v("by")?)), "translate")?,
        "rotate" => moved(m, ops!("rotation", affine::rotation(v("point")?, v("axis")?, f("deg")? * DEG)), "rotate")?,
        "scale" => moved(m, ops!("scaling", affine::scaling(v("centre")?, f("factor")?)), "scale")?,
        "reflect" => moved(m, ops!("reflection", affine::reflection(v("origin")?, v("normal")?)), "reflect")?,
        "union" | "subtract" | "intersect" => {
            let bop = match op.as_str() {
                "union" => BooleanOp::Union,
                "subtract" => BooleanOp::Subtract,
                _ => BooleanOp::Intersect,
            };
            let (_, tool) = m.take(&s("tool")?)?;
            m.boolean(&s("target")?, tool, bop, &op)?;
        }
        other => return Err(format!("op {other} is not in the spec's op set")),
    }
    Ok(())
}

/// Runs a spec file (solidcore/acid.py --kernel-ops writes it from expected/kernel_ops.py): per
/// stage either not run, or its ops then the measurement, one JSON line each.
fn run_spec(path: &str) -> R<()> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
    let spec: Value = serde_json::from_str(&text).map_err(|e| format!("{path}: {e}"))?;
    let stages = spec.get("stages").and_then(Value::as_array).cloned().unwrap_or_default();
    let mut m = Model::new();
    for st in &stages {
        let n = st.get("stage").and_then(Value::as_u64).unwrap_or(0);
        if st.get("run").and_then(Value::as_bool) != Some(true) {
            let why = st.get("detail").and_then(Value::as_str).unwrap_or("not expressible");
            emit(&json!({"stage": n, "not_run": why}));
            continue;
        }
        let t0 = Instant::now();
        let mut r = Ok(());
        for o in st.get("ops").and_then(Value::as_array).cloned().unwrap_or_default() {
            r = spec_op(&mut m, &o);
            if r.is_err() {
                break;
            }
        }
        let secs = t0.elapsed().as_secs_f64();
        if let Err(e) = r {
            emit(&json!({"stage": n, "ok": false, "seconds": secs, "error": e}));
            return Ok(());
        }
        let probes: Vec<[f64; 3]> = st
            .get("probes")
            .and_then(Value::as_array)
            .map(|a| a.iter().map(|p| p3(p.get("p").unwrap_or(&Value::Null))).collect())
            .unwrap_or_default();
        let t1 = Instant::now();
        let meas = measure(&m, &probes);
        emit(&json!({"stage": n, "ok": true, "seconds": secs,
                     "measure_seconds": t1.elapsed().as_secs_f64(), "measured": meas}));
    }
    Ok(())
}

fn emit(v: &Value) {
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{v}");
    let _ = out.flush();
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("--version") {
        emit(&json!({"kernel": "solidcore-ops", "fixes": solidcore::kernel::UpstreamFixes::NAMES.len()}));
        return;
    }
    if args.get(1).map(String::as_str) == Some("--spec") {
        if let Err(e) = session::set_fixes(solidcore::kernel::UpstreamFixes::NAMES.iter().copied()) {
            eprintln!("fixes: {e}");
            std::process::exit(2);
        }
        if let Err(e) = run_spec(args.get(2).map_or("", String::as_str)) {
            eprintln!("{e}");
            std::process::exit(2);
        }
        return;
    }
    let Some(path) = args.get(1) else {
        eprintln!("usage: acid-solidcore <input.json>");
        std::process::exit(2);
    };
    let inp = match read_input(path) {
        Ok(i) => i,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(2);
        }
    };
    // the fixes the app runs the kernel with: every one (crates/smlib's `fixes()`)
    if let Err(e) = session::set_fixes(solidcore::kernel::UpstreamFixes::NAMES.iter().copied()) {
        eprintln!("fixes: {e}");
        std::process::exit(2);
    }
    let stages: Vec<Stage<'_>> = match inp.test.as_str() {
        "acid-1" => acid1(),
        "acid-2" => acid2(&inp),
        "acid-3" => acid3(),
        "acid-4" => acid4(),
        "acid-5" => acid5(),
        "acid-6" => acid6(),
        "acid-7" => acid7(),
        "acid-8" => acid8(),
        "acid-9" => acid9(&inp),
        "acid-10" => acid10(),
        "acid-11" => acid11(&inp),
        t => {
            eprintln!("unknown test {t}");
            std::process::exit(2);
        }
    };
    let mut m = Model::new();
    let n_built = stages.len();
    for (i, s) in stages.iter().enumerate() {
        let n = i + 1;
        let t0 = Instant::now();
        let r = s(&mut m);
        let secs = t0.elapsed().as_secs_f64();
        match r {
            Ok(note) => {
                let t1 = Instant::now();
                let meas = measure(&m, inp.probes.get(i).map_or(&[][..], Vec::as_slice));
                emit(&json!({"stage": n, "ok": true, "seconds": secs,
                             "measure_seconds": t1.elapsed().as_secs_f64(),
                             "measured": meas, "note": note}));
            }
            Err(e) => {
                emit(&json!({"stage": n, "ok": false, "seconds": secs, "error": e}));
                return;
            }
        }
    }
    if inp.test == "acid-11" {
        let why = "the gear (joint.gear) is an assembly constraint of the app; solidcore has no joints or gears";
        emit(&json!({"stage": n_built + 1, "not_run": why}));
        let why = "the driven motion (joint.drive through the gear) is the app's assembly solver; solidcore has none";
        emit(&json!({"stage": n_built + 2, "not_run": why}));
        let t0 = Instant::now();
        let r = acid11_interference(&m);
        let secs = t0.elapsed().as_secs_f64();
        match r {
            Ok(bad) => emit(&json!({"stage": n_built + 3, "ok": true, "seconds": secs,
                                    "interference": bad,
                                    "note": "placements worked out from the 1:3 ratio, not solved by joints; pairs checked with the kernel's contact test (Body::meets, 1e-6)"})),
            Err(e) => emit(&json!({"stage": n_built + 3, "ok": false, "seconds": secs, "error": e})),
        }
    }
}
