# The acid tests

All lengths are in mm and all angles in degrees. Z is up. "Clear of" means the closest distance between the two things named. Every model can be built: none asks for geometry that cannot exist. A refusal, a timeout or a wrong result therefore counts as a failure.

Every test uses only operations that a general CAD program has built in:
- **acid-1 to acid-10:** solid modelling only: primitives, extrude and loft; booleans and patterns; rounds and chamfers; shell and draft; split, mirror and move.
- **acid-11:** adds an assembly, with joints, a gear and a driven motion, using the program's own assembly tools. In FreeCAD that is its built-in Assembly workbench.

There is no sheet metal, and nothing from a plug-in or an add-on.

Each test says why it exists and what it stresses inside a geometric kernel. Where a test came from the [stress corpus](https://github.com/dutchman-cad/stress-corpus), the issue that first showed the failure is named.

---

## acid-1 Seamstress: a half torus with features at its seam

**Why.** Periodic surfaces such as tori, cylinders and spheres have a seam, where the surface parameter wraps from 360° back to 0°. Most kernels store a face that crosses the seam as two pieces, or with a special edge. A feature placed one tolerance away from the seam forces the kernel to decide whether the two are touching. Many kernels then build an edge they later reject. In the stress corpus this broke boss, notch and round operations on half tori at every scale (solidcraft#18274, #18308, #18270).

**What it stresses:**
- intersection curves that run within 1e-5 of a seam;
- deciding whether two things touch, at tolerance;
- rounds that follow a loop interrupted by a notch;
- an inward shell of a torus;
- holes through thin curved walls;
- precision once the part is 10 m from the origin.

1. Make a torus about the Z axis with a Ø60 centre circle and a Ø16 tube. Keep the half with Z ≥ 0, so the body has a flat annular face at Z = 0.
2. On the flat face, add a Ø1.9 × 1.9 cylindrical boss pointing down (−Z). Centre it on the centre circle at Y = 0.95001 (X > 0), so its side is 0.00001 clear of the 0° seam, which lies in the plane Y = 0.
3. In the outer rim of the flat face, cut a Ø1.4 round notch 3 deep at 90° (on +Y), centred on the rim edge.
4. Round both edge loops of the flat face (the inner and the outer circle) with R 0.8. The outer loop now runs around the notch.
5. Shell the body inward to 0.6 walls, removing the boss's end face.
6. Cut 12 vertical Ø1 holes right through the body on the centre circle, every 30° starting at 15°.
7. Split the body with the plane Z = 4 and keep both bodies.
8. Move everything 10000 along +X.

## acid-2 Spline bracelet: rounds, joins, a draft and a shell on a spline outline

**Why.** Real parts have free-form outlines. Their side faces are B-spline surfaces, not planes or cylinders. A round on a B-spline edge has no closed form, so the kernel has to march along it numerically. Every later operation then has to intersect that blend surface with something else. In the stress corpus, rounds on spline outlines took minutes (solidcraft#18294). Joins onto the rounded body were slower still, and drafts of spline sides were refused (#18541).

**What it stresses:**
- rolling-ball blends along a free-form edge;
- booleans between cylinders and blend surfaces;
- a draft of a free-form face;
- an inward shell of a body bounded by spline surfaces and blends;
- a mirror and join that must merge two such bodies.

The outline is the C2 periodic cubic spline through (60, 0), (42, 30), (0, 40), (−42, 30), (−60, 0), (−42, −28), (0, −35) and (42, −28), parameterised by chord length. It is C2 everywhere, including at (60, 0).

Some programs' "periodic interpolation" makes a curve that is only C1 at its first point. That is a different curve, so build this one exactly: its Bézier control points are printed by `python expected/values.py --poles`.

1. Extrude the outline 25 upward from Z = 0.
2. Round the top edge loop with R 3.
3. Join four Ø6 × 10 cylinders standing on the top face, at (±30, ±12).
4. Draft the side face 3° outward, with the bottom face as the neutral plane.
5. Shell the body inward to 1.5, removing the bottom face.
6. Cut a Ø4 hole along Z through each cylinder and the body.
7. Mirror the body across the plane X = 80, giving two bodies.
8. Join the two bodies with a bar from (55, −5, 0) to (105, 5, 5).

## acid-3 Hollow clockwork: a closed shell around small features

**Why.** A shell offsets every face inward by the wall thickness. If a feature is only slightly more than twice the wall wide, the offset faces nearly meet, and the kernel has to work out the new topology. Get it slightly wrong and it either refuses, or leaves a self-intersecting solid. A modelled thread makes this worse, because its offset is a helical sweep. In the stress corpus this was the largest single cause of refusals: 421 items (solidcraft#18239), plus #8509 and #18278.

**What it stresses:**
- offset surfaces that nearly collide;
- a closed internal void, where no face is removed;
- offsets of a helical thread;
- a notch 0.00001 clear of a block corner, which leaves a sliver of material;
- rounds along a loop interrupted by that notch.

1. Make a 80 × 60 × 30 block with one corner at the origin.
2. On the top face, add six square pads 3 × 3 × 2, centred in a 3 × 2 grid at 20 pitch starting at (20, 20).
3. Cut a Ø4.917 hole 20 deep at the top face's centre (40, 30), the minor diameter of M6 × 1. Give its top 15 a modelled M6 × 1 thread.
4. Cut 8 Ø2.5 holes in a circular pattern of radius 12 about that centre, from Z = 32 down to Z = 20, so they also cut the pads they cross.
5. Cut a Ø3 notch 4 deep centred on the top face's front edge, 1.50001 from the left end, so it is 0.00001 clear of the corner.
6. Shell to 1.2 walls with no face removed, leaving a closed internal void.
7. Round every edge of the top face's outer loop with R 0.5.

## acid-4 Crossfire: equal crossing holes and near-coincident cylinders

**Why.** When two cylinders of the same diameter cross, with axes that meet or nearly meet, their intersection curve degenerates: it pinches to a point, or splits into two curves. Kernels that trace intersections numerically lose track there. Two parallel cylinders that overlap by 0.00001 give a sliver face thinner than many kernels' tolerance. In the stress corpus both gave "not a closed solid" (solidcraft#18475, #18235).

**What it stresses:**
- singular surface–surface intersections, from equal radii with axes crossing or nearly crossing;
- the tricylinder at the cube's centre;
- slivers 0.00001 wide;
- a sphere that only just dips into a cylinder's flat end;
- rounds on a cube whose faces are pierced by the holes.

1. Make a 40 × 40 × 40 cube centred on the origin.
2. Cut three Ø18 holes through it along X, Y and Z, all through the centre.
3. Cut two Ø8 holes through it along X, at Y = ±10 and Z = 0, and two along Y, at X = ±10 and Z = 0.00001. Each Y hole crosses an X hole of the same diameter, with axes 0.00001 apart.
4. Make a separate Ø10 cylinder along Z from Z = −30 to 30 at X = 26, which is 1 clear of the cube. Join to it a second one at X = 35.99999, so the two overlap by 0.00001.
5. Join a Ø10 sphere centred at (26, 0, 34.99999), which dips 0.00001 into the first cylinder's top.
6. Round the twelve edges of the cube with R 2. There are now two bodies.

## acid-5 Corner cascade: rounds near their limits, far from the origin

**Why.** Where rounds of nearly equal radius meet at a corner, the kernel has to build a corner patch between surfaces that are almost tangent. A round only slightly larger than a neighbouring chamfer leaves almost no room for its edges. Both are sensitive to floating-point error, and that error grows with distance from the origin. In the stress corpus a second round just under the first worked at the origin but failed 130 mm away (solidcraft#18461, #13551). A round beside a tiny chamfer failed with "unable to map new fillet edge" (#18204).

**What it stresses:**
- corner blends between near-equal radii;
- blends of the order of the tolerance (0.000025 beside 0.00002);
- floating-point precision at 1 m from the origin;
- a shell through blended corners.

1. Make a 30 × 20 × 10 block from (1000, 1000, 1000) to (1030, 1020, 1010).
2. Round the four vertical edges with R 1.3.
3. Round the top face's edge loop with R 1.29, just under the vertical rounds, so the two meet in corner blends at all four top corners.
4. Chamfer the bottom edge along X at Y = 1000 with 0.00002 × 0.00002.
5. Round the bottom edge along Y at X = 1000 with R 0.000025.
6. Shell to 0.4 walls, removing the bottom face.

## acid-6 Needle and planet: extreme size ratios

**Why.** A kernel's tolerances are absolute, typically 1e-7 or 1e-6 mm, but models are not all one size. Put a 0.12 mm feature on a 1000 mm sphere, and the feature is a relative 1e-4 of the part. Meanwhile the sphere's flat is only 0.00025 below its pole. A Ø1 tube bent into a Ø1000 ring has a curvature ratio of 1000 : 1. A 0.02° draft over 1000 mm moves the face by 0.35 mm, but changes its angle by an amount close to the angular tolerance. In the stress corpus these failed or were measured wrong (stress-corpus#109, #114, solidcraft#18288).

**What it stresses:**
- relative precision across five orders of magnitude;
- a plane cutting a sphere just below its pole;
- an offset of a thin, large-radius torus;
- a draft at an angle near the angular tolerance;
- booleans between a huge body and a tiny one.

1. Make a Ø1000 sphere centred on the origin. Cut it with the plane Z = 499.99975, which is 0.00025 below the pole and leaves a flat of Ø1.
2. Join a Ø0.12 × 0.12 cylinder standing on that flat.
3. Make a separate torus about the Z axis with a Ø1000 centre circle and a Ø1 tube. Keep its half with Z ≥ 0, and move it up to Z = 600.
4. Hollow the half ring to 0.06 walls, leaving a closed void.
5. Make a separate Ø1 × 1000 cylinder along Z from (0, 0, −1600) to (0, 0, −600). Draft its side 0.02° inward, with its bottom face as the neutral plane; its top shrinks to about Ø0.30.
6. Join the sphere and the needle with a Ø0.8 rod along Z from (0, 0, −600) to (0, 0, −400). The ring stays separate, so there are two bodies.

## acid-7 Volcano: a draft that runs into holes

**Why.** A draft tilts a face about its neutral edge, so every other edge of the face moves. If holes sit just inside the face's far edge, the tilted face sweeps into them. The kernel must then re-trim the face around each hole, and turn each hole's wall into a notch that breaks out through the side. In the stress corpus, drafts and offsets that ran into holes near an edge were refused (solidcraft#18629, #18301, #18252).

**What it stresses:**
- a draft of a conical face that changes the body's topology, as six holes break out through the side;
- rounds along a loop that runs in and out of those break-outs;
- a pocket and a shell through all of it.

A draft angle is measured from the pull direction (+Z), as CAD programs define it. It is not added to the face's existing taper.

1. Make a cone frustum on the Z axis, Ø200 at Z = 0 and Ø80 at Z = 200. Its side leans 16.70° inward.
2. Cut six Ø10 holes 50 deep into the top face, on a circle of radius 32, which puts them 3 inside the rim.
3. Draft the side face to lean 18° inward, with the bottom face as the neutral plane. The top rim shrinks to radius 35.02, so each hole, which reaches out to radius 37, now breaks through the side near the top.
4. Round every edge of the top face's outer loop with R 0.5. The loop runs in and out of the six break-outs.
5. Cut a Ø120 × 80 cylindrical pocket up into the bottom face.
6. Shell to 2 walls, removing the bottom face.

## acid-8 Lattice drum: patterns on curved faces

**Why.** Patterns multiply everything else. A hole that is easy once becomes 120 holes whose walls must each be intersected with a curved face. Then a shell has to offset all 120 bores, and a split has to cut through a ring of them. It measures robustness at scale, and speed.

**What it stresses:**
- 120 hole-to-cylinder intersections;
- a closed shell with 120 bores;
- bosses along an arc;
- a split through the middle of a ring of holes;
- a mirror into a third body.

1. Make a Ø100 × 120 cylinder on the Z axis, from Z = 0.
2. Cut a Ø6 hole 20 deep along −X into the side at Z = 20, from X = 50 to X = 30. Repeat it as a circular pattern of 24 about Z.
3. Repeat that ring of 24 holes as a linear pattern of 5 up Z, at 20 pitch.
4. Along the arc of radius 30 from (30, 0, 120) to (0, 30, 120), add 7 Ø4 × 3 bosses on the top face.
5. Shell to 2 walls with no face removed.
6. Split with the plane Z = 61, which passes 1 above the middle ring's centres, and keep both bodies.
7. Mirror the upper body across the plane Z = 140, giving three bodies.

## acid-9 Twisted tower: ruled free-form faces against a sphere and a cylinder

**Why.** A loft between a square and the same square turned 45° has four twisted ruled faces, each a hyperbolic paraboloid. Intersecting them with a sphere gives curves that no closed form describes. A bore that passes 0.007 from the twisted faces at mid-height gives a near-tangent intersection between a cylinder and a saddle. Rounding the sphere-to-saddle edges then needs blends between two curved surfaces. This is the class of case where kernels fall back to approximation, and where their tolerances matter most.

**What it stresses:**
- ruled B-spline surfaces;
- sphere–saddle intersections;
- a near-tangent cylinder–saddle intersection;
- blends between two doubly-curved surfaces;
- a shell and a split through all of it.

1. Loft, with straight rulings, from a 40 × 40 square at Z = 0, centred on the Z axis, to the same square turned 45° about Z at Z = 100. Each corner goes to the corner 45° further around.
2. Cut a Ø36.94 hole along Z through the centre. At mid-height the loft's narrowest width is 2 × 18.477, so the bore passes 0.007 from the twisted faces there.
3. Intersect the body with a sphere of radius 55 centred at (0, 0, 50). This trims the eight corners at the top and the bottom.
4. Round every edge where the sphere meets a twisted face with R 1.
5. Shell to 1 wall, removing the top face.
6. Split with the vertical plane through the Z axis at 45° (normal (1, −1, 0)), and keep both bodies.

## acid-10 Near misses: tolerance-sized slivers, 1 km from the origin

**Why.** Every kernel has a tolerance below which two things count as touching. Geometry placed just above it, a dip of 0.00001 or a groove 0.00001 deep, has to be kept, and kept exact, without the kernel merging it away or choking on it. Moving the whole part 1 km from the origin then spends about 20 of a double's 52 bits of precision on position, leaving fewer for those details. The stress corpus's largest surprise was how many failures came down to this alone.

**What it stresses:**
- near-tangent unions: a cylinder lying on a face and a sphere touching a face, each dipping in by 0.00001;
- a 0.00001-deep cut;
- rounds next to all of them;
- a move to 1 km from the origin, which must not lose a detail that small.

1. Make a 50 × 50 × 50 block from the origin.
2. Join a Ø20 × 50 cylinder along X, with its axis at Y = 25, Z = 59.99999, so its side dips 0.00001 into the top face.
3. Join a Ø30 sphere centred at (64.99999, 25, 25), so it dips 0.00001 into the +X face.
4. Cut a groove 0.00001 deep across the −Y face: a box from (20, −1, −1) to (30, 0.00001, 51).
5. Round the four vertical edges of the block with R 5.
6. Move everything by (1000000, 1000000, 0).

## acid-11 Orrery: an assembly with geared joints and a 0.5 mm near miss

**Why.** Assemblies put the kernel to work in another way: parts must be placed through joints, a gear ratio must hold exactly through a full turn, and interference has to be decided between curved parts that come within half a millimetre of each other. A check that works on display meshes reports contact that is not there. A check that is too loose misses contact that is. The parts reuse geometry from acid-1 and acid-2, taken at stages that build in every program measured, so this test measures the assembly rather than those tests again.

**What it stresses:**
- joint placement on circular edges;
- a gear constraint held at every step of a full turn;
- interference between a torus rim and a spline cam whose closest approach is 0.4968, edge to edge in one plane;
- doing it 36 times.

1. Make the base: a 160 × 100 × 10 plate from (−80, −50, 0) to (80, 50, 10), with two Ø6 × 2 pins standing on its top face at (−40, 0) and (13.5, 0). Ground it.
2. Make the ring: acid-1 at stage 3 (the half torus with its boss and notch), moved by (−40, 0, 30) so its flat face is at Z = 30 and its axis passes through the first pin. Join it to the base with a revolute joint about that axis.
3. Make the cam: acid-2 at stage 3 (the extruded outline, its R 3 round and four bosses), scaled by 0.25 about the origin. Cut a Ø2 axle hole through it on its axis (Z, through the origin), and move it by (13.5, 0, 30). Join it to the base with a revolute joint about the axis through the second pin. The outline reaches 15.0032 from its axis, so the cam's tip passes 0.4968 from the ring's rim.
4. Gear the ring to the cam at 1 : 3, so the cam turns three times as fast, the other way.
5. Drive the ring from 0° to 350° in 36 steps of 10°. At every step the cam's angle must be −3 × the ring's, modulo 360°, to 1e-6°.
6. At every step, check every pair of parts for interference. There must be none.
