# The ten acid tests

All lengths are in mm and all angles in degrees. Z is up. "Clear of" means the closest distance between the two things named.

Every model below can be built: none of them asks for a result that cannot exist. A refusal therefore counts as a failure, as does a timeout or a wrong result.

Each test lists the stress-corpus families it combines, by the issue that describes them, so a failure can be traced back.

---

## AT-01 Seamstress: a half torus with features at its seam

*Families: features within 1e-5 of a torus seam (solidcraft#18274, #18308), slow rounds on torus edges (#18270), a shell wall against small features (#18239), and placement far from the origin.*

1. Make a torus about the Z axis with a Ø60 centre circle and a Ø16 tube. Keep the half with Z ≥ 0, so the body has a flat annular face at Z = 0.
2. On the flat face, add a Ø1.9 × 1.9 cylindrical boss pointing down (−Z). Centre it on the centre circle at Y = 0.95001 (X > 0), so its side is 0.00001 clear of the 0° seam, which lies in the plane Y = 0.
3. In the outer rim of the flat face, cut a Ø1.4 round notch 3 deep at 90° (on +Y), centred on the rim edge.
4. Round both edge loops of the flat face (the inner and the outer circle) with R 0.8. The outer loop now runs around the notch.
5. Shell the body inward to 0.6 walls, removing the boss's end face.
6. Cut 12 vertical Ø1 holes right through the body on the centre circle, every 30° starting at 15°.
7. Split the body with the plane Z = 4 and keep both bodies.
8. Move everything 10000 along +X.

## AT-02 Spline bracelet: rounds, joins, drafts and offsets on a spline outline

*Families: slow rounds on a spline outline (solidcraft#18294), slow joins onto a rounded spline body (raykholo's new issue from stress-corpus#101), drafts of spline sides (#18541), and offset faces on a thin spline extrusion (#18510).*

1. Extrude 25 upward from Z = 0 the closed periodic cubic spline through (60, 0), (42, 30), (0, 40), (−42, 30), (−60, 0), (−42, −28), (0, −35) and (42, −28), parameterised by chord length.
2. Round the top edge loop with R 3.
3. Join four Ø6 × 10 cylinders standing on the top face, at (±30, ±12).
4. Draft the side face 3° outward, with the bottom face as the neutral plane.
5. Offset the top face up by 2, carrying the four cylinders with it.
6. Cut a Ø4 hole through each cylinder and the body.
7. Mirror the body across the plane X = 80, giving two bodies.
8. Join the two bodies with a bar from (55, −5, 0) to (105, 5, 5).

## AT-03 Hollow clockwork: a closed shell around small features

*Families: a shell wall reaching a small feature's half-size (solidcraft#18239, #18345), a shell after a modelled thread (#8509), a closed shell after a hole pattern (#18278), and a rim notch next to the shell (#18285).*

1. Make a 80 × 60 × 30 block with one corner at the origin.
2. On the top face, add six square pads 3 × 3 × 2, in a 3 × 2 grid at 20 pitch starting at (20, 20).
3. Cut a Ø6 hole 20 deep at the top face's centre, and give it a modelled M6 × 1 thread 15 long.
4. Cut 8 Ø2.5 holes in a circular pattern of radius 12 about that centre, from Z = 32 down to Z = 20, so they also cut the pads they cross.
5. Cut a Ø3 notch 4 deep centred on the top face's front edge, 1.50001 from the left end, so it is 0.00001 clear of the corner.
6. Shell to 1.2 walls with no face removed, leaving a closed internal void.
7. Round every edge of the top face's outer loop with R 0.5.

## AT-04 Crossfire: equal crossing holes and near-coincident cylinders

*Families: equal crossing holes offset by about the tolerance (solidcraft#18475), and parallel cylinders overlapping by about the tolerance (#18235).*

1. Make a 40 × 40 × 40 cube centred on the origin.
2. Cut three Ø18 holes through it along X, Y and Z, all through the centre.
3. Cut two Ø8 holes through it along X, at Y = ±10 and Z = 0, and two along Y, at X = ±10 and Z = 0.00001. Each Y hole crosses an X hole of the same diameter, with axes 0.00001 apart.
4. Make a separate Ø10 cylinder along Z from Z = −30 to 30 at X = 26, which is 1 clear of the cube, and join to it a second one at X = 35.99999, so the two overlap by 0.00001.
5. Join a Ø10 sphere centred at (26, 0, 34.99999), which dips 0.00001 into the first cylinder's top.
6. Round the twelve edges of the cube with R 2. There are now two bodies.

## AT-05 Corner cascade: rounds near their limits, far from the origin

*Families: a second round just under the first's radius, which works at the origin and fails away from it (solidcraft#18461, #13551); a round next to a chamfer only slightly larger than it (#18204).*

1. Make a 30 × 20 × 10 block from (1000, 1000, 1000) to (1030, 1020, 1010).
2. Round the four vertical edges with R 1.3.
3. Round the top face's edge loop with R 1.29, just under the vertical rounds, so the two meet in corner blends at all four top corners.
4. Chamfer the bottom edge along X at Y = 1000 with 0.00002 × 0.00002.
5. Round the bottom edge along Y at X = 1000, which meets that chamfer at the corner, with R 0.000025.
6. Shell to 0.4 walls, removing the bottom face.

## AT-06 Needle and planet: extreme size ratios

*Families: a sphere cut just below its pole (stress-corpus#109), drafts over extreme ratios (#114), and a Ø1000 ring with a Ø1 tube (solidcraft#18288).*

1. Make a Ø1000 sphere centred on the origin. Cut it with the plane Z = 499.99975, which is 0.00025 below the pole and leaves a flat of Ø1.
2. Join a separate Ø0.12 × 0.12 cylinder standing on that flat.
3. Make a separate torus about the Z axis with a Ø1000 centre circle and a Ø1 tube, keep its half with Z ≥ 0, and move it up to Z = 600.
4. Hollow the half ring to 0.06 walls, leaving a closed void.
5. Make a separate Ø1 × 1000 cylinder along Z from (0, 0, −1600) to (0, 0, −600), and draft its side 0.02° inward with its bottom face as the neutral plane. Its top shrinks to about Ø0.30.
6. Join the sphere and the needle with a Ø0.8 rod along Z from (0, 0, −600) to (0, 0, −400). The ring stays separate, so there are two bodies.

## AT-07 Volcano: offsets and drafts that run into holes

*Families: offset faces whose loops cross (solidcraft#18301), and drafts past a hole near the top edge (#18629, #18252).*

1. Make a cone frustum on the Z axis, Ø200 at Z = 0, Ø80 at Z = 200.
2. Cut 6 Ø10 holes 50 deep into the top face, on a circle of radius 32, which puts them 3 inside the rim.
3. Offset the top face outward by 5.
4. Draft the side face 2° outward, with the bottom face as the neutral plane.
5. Cut a Ø120 × 80 cylindrical pocket up into the bottom face.
6. Shell to 2 walls, removing the bottom face.

## AT-08 Lattice drum: patterns on curved faces

*Families: circular, linear and path patterns, a closed shell after a pattern, and a split through a pattern.*

1. Make a Ø100 × 120 cylinder on the Z axis, from Z = 0.
2. Cut a Ø6 hole 20 deep along −X into the side at Z = 20, from X = 50 to X = 30. Repeat it as a circular pattern of 24 about Z.
3. Repeat that ring of 24 holes as a linear pattern of 5 up Z, at 20 pitch.
4. Along the arc of radius 30 from (30, 0, 120) to (0, 30, 120), add 7 Ø4 × 3 bosses on the top face.
5. Shell to 2 walls with no face removed.
6. Split with the plane Z = 61, which passes 1 above the middle ring's centres, and keep both bodies.
7. Mirror the upper body across the plane Z = 140, giving three bodies.

## AT-09 Origami: sheet metal with blends after bends

*Families: flanges, folds and holes across bends; and a round of a loop that includes a bend's tangent edge (stress-corpus S1783).*

1. Make a 1 thick sheet from a 120 × 80 rectangle, with a bend radius of 1 and a K-factor of 0.44.
2. Fold the sheet along the line X = 60 by 30°, with a radius of 2.
3. Add 90° flanges 20 tall on the two short edges, at X = 0 and X = 120.
4. Cut a 10 × 40 slot across the X = 0 flange's bend, centred on it.
5. Round the long edge on the +Y side, which runs across the fold's two tangent edges, with R 0.4.
6. Flatten the part and check the flat pattern's area.

## AT-10 Orrery: an assembly whose parts carry the other nine tests' features

*Families: assembly joints, gear ratios, motion, and interference, built on parts that are themselves hard.*

1. Make the base: AT-03's hollow block at stage 6.
2. Make the ring: AT-01's half torus at stage 5, moved so it is centred over the base.
3. Make the cam: AT-02's body at stage 4, scaled by 0.25.
4. Make the gear: a Ø40 × 6 disc with 24 square teeth, 2 × 2 × 6, in a circular pattern.
5. Joints: the ring is revolute about Z on the base; the cam is revolute about an axis parallel to Z at (60, 0); the gear is cylindrical on that same axis. Gear the ring to the cam at 1 : 3.
6. Drive the ring through 360° in 36 steps, and check that the cam has turned 1080°.
7. Report the interference between every pair of parts at every step: there should be none.
