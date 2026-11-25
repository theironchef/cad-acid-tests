# Kernel ops: one spec, two kernels

[`expected/kernel_ops.py`](../expected/kernel_ops.py) lists each acid test's stages as neutral B-rep operations. Two harnesses run exactly that list, with the same ops, in the same order and with the same numbers:

- the kernel-near Fusion row (`tools/run_fusion_kernel.py` with `fusion/acid_kernel.py`), on Fusion's transient B-rep API;
- the kernel-only SolidCraft row (`solidcore/`), on `solidcore-ops`.

Every number is computed once, in Python, and both harnesses take the floats as they are, cos 15° included.

The op set is what both kernels can do directly, with no features, sketches or history. A stage that needs anything else is marked `not_expressible` and is "not run" on both rows. So is every stage after it, because each stage builds on the one before.

Lengths are mm. Fusion's API works in cm, so `fusion/acid_kernel.py` multiplies every length by 0.1.

## Op mapping

| Neutral op | Fusion (`adsk.fusion.TemporaryBRepManager`, `T`) | solidcore-ops |
|---|---|---|
| `box(out, corner, size)` | `T.createBox(OrientedBoundingBox3D.create(centre, (1,0,0), (0,1,0), sx, sy, sz))` | `make_box(corner, size)` |
| `cylinder(out, base, axis, r, h)` | `T.createCylinderOrCone(base, r, base + h·axis, r)` | `make_cylinder([0;3], r, h)`. When the axis is not +Z: `Body::transform(affine::rotation([0;3], Z×axis, acos(axis·Z)))`, then `Body::transform(affine::translation(base))`. When it is +Z: `make_cylinder(base, r, h)` |
| `cone(out, base, Z, r1, r2, h)` | `T.createCylinderOrCone(base, r1, base + h·Z, r2)` | `make_cone(base, r1, r2, h)` |
| `sphere(out, c, r)` | `T.createSphere(c, r)` | `make_sphere(c, r)` |
| `torus(out, c, Z, R, r)` | `T.createTorus(c, Vector3D(Z), R, r)` | `make_torus(c, R, r)` |
| `copy(src, out)` | `T.copy(body)` | `Body::copy()` |
| `translate(bodies, by)` | `T.transform(body, Matrix3D)`, translation set with `setWithArray` | `Body::transform(&affine::translation(by))` |
| `rotate(bodies, point, axis, deg)` | `Matrix3D.setToRotation(rad, axis, point)`, then `T.transform` | `Body::transform(&affine::rotation(point, axis, rad))` |
| `scale(bodies, centre, f)` | `Matrix3D.setWithArray([f·I \| c(1−f)])`, then `T.transform` | `Body::transform(&affine::scaling(centre, f))` |
| `reflect(bodies, origin, normal)` | `Matrix3D.setWithArray([I − 2nnᵀ \| 2(o·n)n])`, then `T.transform`. A mirrored temporary body stays a valid solid (checked in Fusion 2706.1.17) | `Body::transform(&affine::reflection(origin, normal))` |
| `union(target, tool)` | `T.booleanOperation(target, tool, UnionBooleanType)` | `boolean(a, b, BooleanOp::Union)` |
| `subtract(target, tool)` | `T.booleanOperation(target, tool, DifferenceBooleanType)` | `boolean(a, b, BooleanOp::Subtract)` |
| `intersect(target, tool)` | `T.booleanOperation(target, tool, IntersectionBooleanType)` | `boolean(a, b, BooleanOp::Intersect)` |
| `rename(src, out)` | (harness bookkeeping) | (harness bookkeeping) |

Notes on the booleans and on two composite steps:

- **Booleans.** Fusion's `booleanOperation` changes the target in place and returns a bool. ops' `boolean` takes both bodies and returns `Option<Body>`. In both harnesses the tool is consumed, and an empty result is an error.
- **Split by a plane.** TemporaryBRepManager has no split. A split is therefore written as `copy` plus two `intersect`s with half-space boxes, one per side; acid-9's 45° plane uses boxes rotated 45° about Z. Both rows run those same ops. `solidcore-ops::split::split_body` is not used.
- **Patterns.** Each instance is cut or joined one at a time, in order. Neither row uses `union_all`, whose pairwise order would differ from Fusion's.

## Measuring

| Check | Fusion | solidcore-ops / solidcore |
|---|---|---|
| bodies | `BRepBody.lumps.count`, summed | material census (`brep_material_census`), summed |
| valid | `BRepBody.isSolid` for every body | `brep_is_closed_touching_solids` and `Body::check(CheckLevel::Geometry)` |
| volume | `getPhysicalProperties(VeryHighCalculationAccuracy).volume` of a copy put in a scratch direct-modelling design (`DesignTypes.DirectDesignType`: no timeline), which is closed unsaved straight away. `BRepBody.volume` is kept as `tbm_volume_mm3` | `Body::mass_properties(1e-8).volume` |
| probes | `BRepBody.pointContainment(p)`: inside / on / outside | `Body::classify_point(p)`: Inside / OnBoundary / Outside |

Why Fusion's volume goes through a scratch design:

- A temporary body's `getPhysicalProperties` returns 0 at every accuracy.
- `BRepBody.volume` is Fusion's coarse estimate. On the same body it is 1e-5 relative off the very-high-accuracy value: 27.9 mm³ on acid-8 stage 2 and 0.98 mm³ on acid-4 stage 3. That is outside the checks' slack.

A probe point the kernel puts on the boundary fails its probe on both rows. There are no probe cubes.

## Outside the intersection

| Missing op | Fusion | solidcore-ops | Stages |
|---|---|---|---|
| round / chamfer | no blend in TemporaryBRepManager | `blend::blend` | acid-1 s4, acid-2 s2, acid-3 s7, acid-4 s6, acid-5 s2–5, acid-7 s4, acid-9 s4, acid-10 s5 |
| shell | no offset | `offset::shell` | acid-1 s5, acid-2 s5, acid-3 s6, acid-5 s6, acid-6 s4, acid-7 s6, acid-8 s5, acid-9 s5 |
| draft | none | `offset::draft` | acid-2 s4, acid-6 s5, acid-7 s3 |
| extrude of a spline profile | no extrude; `createWireFromCurves` takes only lines, arcs, circles and ellipses | `sweep::extrude` | acid-2 s1, and acid-11 s3 through the cam |
| loft with straight rulings | `createRuledSurface` makes a sheet. With `createFaceFromPlanarWires` caps and a union it is still a sheet, with volume 0 and `isSolid` false (checked), and there is no stitch | `sweep::loft` | acid-9 s1 |
| modelled thread | `createHelixWire`, but no sweep | `sweep::screw_sweep` | acid-3 s3 |
| joints, gear, driven motion | none | none | acid-11 s4–6 |

`adsk.fusion.BRepBodyDefinition`, which builds a body from explicit faces, edges and curves, could make the extrusion and the loft. It is a different kind of operation from ops' `extrude` and `loft`, though, so the stages are left out rather than run differently on each row.

What both rows run, under spec version 1:

- acid-1 s1–3
- acid-3 s1–2
- acid-4 s1–5
- acid-5 s1
- acid-6 s1–3
- acid-7 s1–2
- acid-8 s1–4
- acid-10 s1–4
- acid-11 s1–2 (the ring is placed by its move; the joint is not run)

That is 26 stages. acid-2 and acid-9 run nothing.
