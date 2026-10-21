// =====================================================================
//  Echoes of Termina  —  40 mm speaker case  (2 parts, glued)
// =====================================================================
//
//  Driver under design:  2 W / 8 ohm, 40 mm frame diameter, 20 mm deep,
//  glued directly into the baffle (no screws, non-serviceable by design).
//
//  Architecture
//  ------------
//  A single rotational (round) case split by one plane perpendicular to
//  the acoustic axis:
//
//      PART A  "front bezel"   z = 0 .. 16    front grille plate,
//                                             driver seat, male spigot
//      PART B  "back cup"      z = 12 .. 35   female rim (lap), tube,
//                                             rear wall, cable boss
//
//  The two parts overlap by `lap_len` at z = joint_z: part A's reduced
//  outer end (the spigot) slides inside part B's full-diameter rim.  That
//  gives a self-aligning lap (rabbet) joint with
//     * a cylindrical glue face  (alignment + shear strength)
//     * a flat annular glue face (the actual seal)
//  which is airtight when glued with a continuous bead of CA / epoxy.
//
//  Part B's rear face also carries two features:
//     * a KEYHOLE HANGER so the case mounts on a wall screw (6 mm head).
//       This is an UNDERCUT pocket: a keyhole-shaped mouth `key_lip` thick
//       with a wider cavity behind it.  A prismatic keyhole (constant
//       section) cannot work — the head is coaxial with the shank, so it
//       could never reach the narrow slot and the case would lift straight
//       back off.  See the longer comment at the keyhole modules.
//     * a CABLE GUIDE: a bend well plus radial channels out to the rim, so
//       the wire is not pinched between the case and the wall
//  Both are BLIND recesses.  A normal keyhole is a through-slot, which on a
//  sealed box would leak straight into the chamber, so each gets an internal
//  pad behind it and keeps a floor (asserted below).
//
//  Both parts print with NO supports:  part A is printed front-face down,
//  part B is flipped so its rear wall sits on the bed (cup opening up).
//
//  FDM / 3D-printing notes
//  -----------------------
//   * Both glue faces end up at the TOP of their print — no elephant's foot
//     on a mating surface, and top surfaces print truest.
//   * Every overhang in the model is exactly 45 deg (all chamfers are 1:1),
//     which is the usual no-support limit.  If your printer struggles at
//     45 deg these edges are only 0.5-0.8 mm tall and will bridge anyway.
//   * Part A's whole first layer is connected (outer ring + hub + spokes +
//     rings all touch the bed) — good adhesion, no floating grille parts.
//   * Thinnest feature is part A's rear face annulus, ~1.1 mm = 2-3 extrusion
//     widths at 0.4 mm; printable, but if your slicer under-fills it, reduce
//     seat_lead_in / lap_lead_in.
//   * 2.4 mm walls = 5-6 perimeters at 0.4 mm, which is what makes the box
//     airtight.  Do NOT use vase/spiral mode.
//
//  Suggested slicer settings (0.4 mm nozzle)
//  ----------------------------------------
//     layer height .......... 0.20 mm
//     perimeters ............ 4 or more (walls are 2.4 mm)
//     top/bottom layers ..... 5
//     infill ................ 15-25 % (the parts are mostly wall already)
//     supports .............. none
//     orientation ........... part A: grille face on the bed
//                             part B: rear face on the bed (as exported)
//     filament .............. PLA or PETG; ~25 g total
//     glue .................. CA (cyanoacrylate) or 5-min epoxy, continuous
//                             bead, then let it cure before handling
//
//  All dimensions are millimetres.  Axis = +Z, Z=0 is the front face.
//  Verify the three "measure yours" values against the real driver before
//  printing: drv_od, drv_flange_h, drv_cone_d.
// =====================================================================

/* [What to build] */
part_customizer = "assembly"; // [assembly, front, back, all, section, exploded, cavity, driver, driver_full, sec2d, sec2d_drv]
// Select the output from the OpenSCAD Customizer ("part_customizer"), or
// override it from a two-line wrapper file:
//     PART = "front";
//     include <speaker_case_40mm.scad>
part = is_undef(PART) ? part_customizer : PART;

/* [Driver — measure yours before printing] */
drv_od        = 40.0;  // frame outer diameter
drv_depth     = 20.0;  // front rim -> back of magnet (total depth)
drv_flange_h  = 3.0;   // height of the full-diameter frame wall = seat depth
drv_cone_d    = 36.0;  // radiating opening that must stay unobstructed
drv_body_d    = 32.0;  // effective dia. of cone/basket/magnet behind the
                       //   flange — used ONLY for the air-volume model.
                       //   Deliberately conservative (real basket is open).

/* [Case] */
case_od       = 50.0;  // outside diameter
wall          = 2.4;   // side-wall thickness (6 perimeters at 0.4 mm)
front_t       = 3.0;   // front plate (grille) thickness
back_t        = 3.0;   // rear wall thickness
case_len      = 35.0;  // TOTAL assembled length  <-- main tuning knob (volume)
seat_gap      = 0.20;  // radial glue clearance, driver frame -> seat bore
                       //   FDM slip-fit rule of thumb: 0.15-0.25 mm per side.
                       //   0.20 suits an uncalibrated hobby printer; tighten to
                       //   0.15 only if your holes print true to size.
front_chamfer = 0.8;   // cosmetic chamfer, front outer edge
back_chamfer  = 0.8;   // cosmetic chamfer, rear outer edge

/* [Glue joint] */
joint_z       = 12.0;  // joint plane, from the front face
lap_len       = 4.0;   // lap (overlap) length
lap_gap       = 0.20;  // radial clearance, spigot -> rim bore (glue line)
seat_lead_in  = 0.5;   // funnel at the seat-bore entry (driver insertion)
lap_lead_in   = 0.6;   // 45 deg lead-in on the spigot (case assembly)
rim_lead_in   = 0.6;   // 45 deg lead-in inside part B's rim mouth

/* [Front grille] */
grille        = true;  // false = fully open Ø36 aperture
spokes        = 6;
spoke_w       = 2.5;
hub_d         = 8.0;
ring_rs       = [9.0, 14.0];
ring_w        = 2.0;

/* [Cable exit] */
wire_d        = 4.0;   // hole diameter (seal with hot glue or a grommet)
wire_off      = 16.0;  // distance from the axis (clear of the mount pad)
wire_ang      = 270;   // degrees; 270 = -Y ("down" in the assembled model)
boss_wall     = 2.5;   // glue boss wall around the hole
boss_h        = 2.5;   // boss height (lengthens the glue throat)
wire_cs       = 0.8;   // 45 deg countersink at each end of the cable hole

/* [Wall mount - keyhole hanger] */
// The pocket is an UNDERCUT, in two layers:
//   mouth  : keyhole outline, `key_lip` thick, through the rear face
//   cavity : wider void behind it, where the head actually travels
// A prismatic (constant-section) keyhole does NOT work: the head is coaxial
// with the shank, so it can never reach the narrow slot — the case would just
// lift straight back off.  The head must sit BEHIND a narrow mouth.
mount         = true;  // rear-face keyhole so the case hangs on a wall screw
key_head_d    = 6.0;   // screw head diameter (the head is what gets captured)
key_shank_d   = 4.0;   // screw shank diameter (sets the slot width)
key_entry_d   = 8.4;   // entry hole: head + clearance, easy to align
key_slot_w    = 4.4;   // must clear the shank; the lip retains by
                       //   (key_head_d - key_slot_w)/2 = 0.8 mm per side
key_slot_len  = 8.0;   // travel from the entry position to the seated position
key_depth     = 5.0;   // total pocket depth from the rear face
key_lip       = 1.5;   // thickness of the retaining lip at the mouth
key_clear     = 0.4;   // radial clearance around the head in its cavity
key_web       = 1.5;   // material left behind the pocket floor
key_pad_margin= 1.5;   // internal pad overhang beyond the head cavity
key_up        = 90;    // slot direction when hung; 90 = straight up

/* [Cable guide on the wall face] */
wire_well_d    = 14.0; // bend pocket around the cable exit
wire_well_deep = 2.2;  // depth of that pocket
well_pad_d     = 16.0; // internal pad under the pocket
well_pad_h     = 1.0;  // its height
guide_dirs     = [-40, 0, 40];  // radial channel angles, relative to wire_ang
guide_w        = 3.4;  // radial channel width
guide_deep     = 2.0;  // radial channel depth

/* [Quality] */
$fn = 96;

// =====================================================================
//  Derived geometry
// =====================================================================
or_          = case_od / 2;            // 25.00  outer radius
ir_          = or_ - wall;             // 22.60  part B bore radius
seat_r       = drv_od / 2 + seat_gap;  // 20.15  driver seat bore radius
spigot_r     = ir_ - lap_gap;          // 22.45  part A spigot radius
z_joint_end  = joint_z + lap_len;      // 16.00  end of part A
z_int_end    = case_len - back_t;      // 32.00  inner face of the rear wall
z_drv_back   = front_t + drv_depth;    // 23.00  rear extent of the driver
front_open_r = drv_cone_d / 2;         // 18.00
boss_or      = wire_d / 2 + boss_wall;
wx           = wire_off * cos(wire_ang);
wy           = wire_off * sin(wire_ang);
ext          = 1.5;                    // cutter over-run, see chamfer_cut()

// --- keyhole hanger derived
// Both 2D outlines are built with the ENTRY CIRCLE at the local origin, then
// shifted down by key_cy so the outline's centroid lands on the case axis
// (keeps the hanging load centred and the shape symmetric about r = 0).
key_cy       = (key_slot_len - key_entry_d / 2) / 2;   // 1.90 centroid offset
key_cav_w    = key_head_d + 2 * key_clear;             // 6.80 head cavity width
// Head travel, measured in the local frame (entry circle centre at y = 0):
//   inserted: head centred on the entry circle, spanning -head_r .. +head_r
//   seated  : shank resting on the top of the slot, so the head centre sits
//             at slot_len - shank_r and the head reaches shank_r further up
key_y_lo     = -key_head_d / 2 - key_clear;                             // -3.40
key_y_hi     = key_slot_len - key_shank_d / 2 + key_head_d / 2 + key_clear; // 9.40
key_cav_h    = key_depth - key_lip;                    // 3.50 head cavity height
// Pad is a stadium that follows the head cavity, so it needs far less material
// than a full disc and cannot wander into the cable well.  NOTE: y_lo/y_hi are
// in the LOCAL frame (entry circle at the origin), same as the cavity, because
// key_place() applies the centroid shift to all of them exactly once.
key_pad_w    = key_cav_w + 2 * key_pad_margin;         // 9.80
key_pad_ylo  = key_y_lo - key_pad_margin;              // -4.90 local
key_pad_yhi  = key_y_hi + key_pad_margin;              // 10.90 local
key_pad_h    = key_depth - back_t + key_web;           // 3.50
key_lip_w    = (key_head_d - key_slot_w) / 2;          // 0.80 retaining lip
key_pad_lo   = key_pad_ylo - key_cy;                   // -6.80 reach in the case frame
key_pad_hi   = key_pad_yhi - key_cy;                   //  9.00 reach in the case frame

// --- wall-face feature floors (material left between recess and chamber)
key_floor    = back_t + key_pad_h - key_depth;         // = key_web
well_floor   = back_t + well_pad_h - wire_well_deep;   // 1.80
guide_floor  = back_t + well_pad_h - guide_deep;       // 2.00 over the pad
guide_floor_bare = back_t - guide_deep;                // 1.00 off the pad

// ---- design-rule checks -------------------------------------------------
assert(wall >= 1.8,               "wall too thin for a sealed, glued case");
assert(spigot_r - seat_r >= 1.6,  "spigot wall too thin — increase case_od");
assert(spigot_r < ir_,            "lap clearance must be positive");
assert(seat_r > drv_od / 2,       "seat clearance must be positive");
assert(joint_z > front_t + drv_flange_h, "joint plane collides with the driver seat");
assert(z_drv_back < z_int_end,    "no air volume left behind the driver");
assert(front_open_r < seat_r,     "grille aperture wider than the seat shoulder");
assert(ir_ > drv_od / 2,          "driver cannot pass through part B's bore");
assert(boss_wall >= 1.6,          "cable boss wall too thin");
assert(spigot_r - lap_lead_in > seat_r + seat_lead_in,
       "spigot rear face disappears — reduce a lead-in");

// --- wall-mount / cable-guide design rules
assert(!mount || key_pad_h >= 0.8,
       "mount pad too short — reduce key_depth or increase back_t");
assert(!mount || key_floor >= 0.8, "keyhole pocket breaks through the rear wall");
assert(!mount || key_cav_h >= key_head_d / 2,
       "head cavity too shallow — increase key_depth");
assert(!mount || key_lip >= 1.0,   "retaining lip too thin");
assert(!mount || key_lip_w >= 0.6,
       "retaining lip too narrow — the slot is too close to the head diameter");
assert(!mount || key_entry_d > key_head_d,
       "screw head will not pass through the entry hole");
assert(!mount || key_slot_w > key_shank_d,
       "slot too narrow for the screw shank");
assert(well_floor >= 0.8,          "cable well breaks through the rear wall");
assert(guide_floor_bare >= 0.8,    "cable channel breaks through the rear wall");
assert(wire_off - wire_well_d / 2 >= -key_pad_lo,
       "cable well reaches into the mount pad — move wire_off out or shrink the well");
assert(!mount || key_pad_margin >= 0.8,
       "mount pad margin too small — the pocket floor could break out at its edge");

// ---------------------------------------------------------------------
// 45 deg chamfer cutter.
//
// Cuts a conical face that passes exactly through (z_a, r_a) and (z_b, r_b),
// continuing `ext` mm past each end at the same slope.  The over-run matters:
// a cone that merely touches the body tangentially makes CGAL emit a sliver
// shell separated from the part by the over-run distance.
// ---------------------------------------------------------------------
module chamfer_cut(z_a, r_a, z_b, r_b) {
  s = (r_b - r_a) / (z_b - z_a);
  translate([0, 0, z_a - ext])
    cylinder(r1 = r_a - s * ext, r2 = r_b + s * ext,
             h = (z_b - z_a) + 2 * ext);
}

// =====================================================================
//  Driver, modelled as a keep-out solid (never printed, only ghosted)
// =====================================================================
module driver_keepout() {
  translate([0, 0, front_t]) {
    cylinder(r = drv_od / 2, h = drv_flange_h);                 // frame wall
    translate([0, 0, drv_flange_h])
      cylinder(r = drv_body_d / 2, h = drv_depth - drv_flange_h);
  }
}

// =====================================================================
//  PART A — front bezel
//
//  Built as ONE exact revolved profile rather than a stack of cylinders with
//  chamfer cones subtracted.  Subtracting near-tangent cones is what produced
//  0.01 mm sliver shells in the first draft of this model; a profile has
//  every face meeting its neighbours on a shared edge by construction.
// =====================================================================
function profile_a() = [
  [front_open_r,            0],                        // aperture, front face
  [or_ - front_chamfer,     0],                        // front face
  [or_,                     front_chamfer],            // front outer chamfer
  [or_,                     joint_z],                  // outer wall
  [spigot_r,                joint_z],                  // rear face at the joint
  [spigot_r,                z_joint_end - lap_lead_in],// spigot outer wall
  [spigot_r - lap_lead_in,  z_joint_end],              // spigot lead-in
  [seat_r + seat_lead_in,   z_joint_end],              // rear face (0.9 mm)
  [seat_r,                  z_joint_end - seat_lead_in],// seat bore funnel
  [seat_r,                  front_t],                  // seat bore wall
  [front_open_r,            front_t]                   // seat shoulder
];

module grille_ribs() {
  // Spokes deliberately run ~1 mm PAST the aperture radius so they fuse into
  // the surrounding plate instead of leaving a slicer-visible hairline gap.
  cylinder(d = hub_d, h = front_t);                            // hub
  for (i = [0 : spokes - 1])                                   // spokes
    rotate([0, 0, i * 360 / spokes])
      translate([hub_d / 2 - 0.6, -spoke_w / 2, 0])
        cube([front_open_r + 1.0 - (hub_d / 2 - 0.6), spoke_w, front_t]);
  for (rr = ring_rs)                                           // rings
    difference() {
      cylinder(r = rr + ring_w / 2, h = front_t);
      cylinder(r = rr - ring_w / 2, h = front_t);
    }
}

module part_a() {
  union() {
    rotate_extrude(convexity = 4) polygon(profile_a());
    // Added AFTER the profile: the ribs live inside the aperture, so the
    // revolved profile alone would leave them out (or cut them away).
    if (grille) grille_ribs();
  }
}

// =====================================================================
//  PART B — back cup  (also one exact revolved profile)
// =====================================================================
function profile_b() = [
  [ir_,                      joint_z + rim_lead_in],   // rim mouth lead-in
  [ir_ + rim_lead_in,        joint_z],                 // rim mouth
  [or_,                      joint_z],                 // rim front glue face
  [or_,                      case_len - back_chamfer], // outer wall
  [or_ - back_chamfer,       case_len],                // rear outer chamfer
  [0,                        case_len],                // rear face
  [0,                        z_int_end],               // inner rear face
  [ir_,                      z_int_end]                // bore wall
];

// ---------------------------------------------------------------------
//  Rear-face features: keyhole hanger + cable bend guide.
//
//  Both are BLIND recesses.  A conventional keyhole is a through-slot, which
//  on a sealed box would be a permanent air leak straight into the chamber —
//  so the pocket gets an internal pad behind it and keeps a `key_web` floor.
//  Same idea for the cable channel: an internal pad under the well, and the
//  floors are asserted above so neither can ever break through.
//
//  The hanger is an UNDERCUT pocket in two layers:
//
//     z = case_len          +-----------------------------+
//                           |  LIP   (keyhole outline)    |  key_lip thick
//     z = case_len-key_lip  +-----------------------------+
//                           |  CAVITY (head travels here) |  key_cav_h thick
//     z = case_len-key_depth+-----------------------------+
//                           |  floor (key_web)            |
//
//  The lip's slot is narrower than the screw head, so once the case settles
//  the head cannot come back out — it is trapped behind the lip.  A pocket of
//  constant cross-section cannot do this: the head is coaxial with the shank,
//  so it would have to fit inside the narrow slot to reach the seated
//  position, and it would simply lift back out through the entry hole.
// ---------------------------------------------------------------------
// 2D outline of the MOUTH: entry circle + slot, entry circle at the origin.
module key_mouth_2d() {
  circle(d = key_entry_d);
  translate([0, key_slot_len / 2])
    square([key_slot_w, key_slot_len], center = true);
}

// 2D footprint of the CAVITY: a stadium long enough to hold the head both at
// the entry position and at the fully seated position.
module key_cavity_2d() {
  hull() {
    translate([0, key_y_lo + key_cav_w / 2]) circle(d = key_cav_w);
    translate([0, key_y_hi - key_cav_w / 2]) circle(d = key_cav_w);
  }
}

// Places either outline: centre the centroid on the axis, then aim the slot.
module key_place() {
  rotate([0, 0, key_up - 90]) translate([0, -key_cy]) children();
}

module keyhole_mouth() {                       // outermost layer
  translate([0, 0, case_len - key_lip])
    linear_extrude(height = key_lip)
      key_place() key_mouth_2d();
}

module keyhole_cavity() {                      // undercut behind the lip
  translate([0, 0, case_len - key_depth])
    linear_extrude(height = key_cav_h)
      key_place() key_cavity_2d();
}

// Every solid that protrudes into the chamber.  Used BOTH to build part B and
// to carve the cavity solid, so the volume model cannot drift from the real
// geometry.  Each extends 1 mm past z_int_end to merge into the rear wall.
module internal_bosses() {
  translate([wx, wy, z_int_end - boss_h])                 // cable glue boss
    cylinder(r = boss_or, h = boss_h + 1);
  if (mount)                                              // keyhole pad
    translate([0, 0, z_int_end - key_pad_h])
      linear_extrude(height = key_pad_h + 1)
        key_place()
          hull() {
            translate([0, key_pad_ylo + key_pad_w / 2]) circle(d = key_pad_w);
            translate([0, key_pad_yhi - key_pad_w / 2]) circle(d = key_pad_w);
          }
  translate([wx, wy, z_int_end - well_pad_h])             // cable-well pad
    cylinder(d = well_pad_d, h = well_pad_h + 1);
}

module rear_recesses() {
  if (mount) {
    keyhole_mouth();
    keyhole_cavity();
  }

  // bend pocket: lets the cable turn 90 deg out of the hole without pinching
  translate([wx, wy, case_len - wire_well_deep])
    cylinder(d = wire_well_d, h = wire_well_deep + 1);

  // radial channels: run the cable out to the rim in any of guide_dirs
  for (a = guide_dirs)
    rotate([0, 0, wire_ang - 90 + a])
      translate([0, 0, case_len - guide_deep])
        linear_extrude(height = guide_deep + 1)
          translate([-guide_w / 2, wire_off - wire_well_d / 2])
            square([guide_w, or_ + 2 - (wire_off - wire_well_d / 2)]);
}

module part_b() {
  difference() {
    union() {
      rotate_extrude(convexity = 4) polygon(profile_b());
      internal_bosses();
    }

    // cable hole, punched clear through wall + boss
    translate([wx, wy, z_int_end - boss_h - 1])
      cylinder(d = wire_d, h = back_t + boss_h + 2);

    // 45 deg countersinks at both ends of the hole, for a glue fillet
    translate([wx, wy, 0])
      chamfer_cut(case_len - wire_cs, wire_d / 2,
                  case_len,          wire_d / 2 + wire_cs);
    translate([wx, wy, 0])
      chamfer_cut(z_int_end - boss_h + wire_cs, wire_d / 2,
                  z_int_end - boss_h,          wire_d / 2 + wire_cs);

    rear_recesses();
  }
}

module case_only() { part_a(); part_b(); }

// Part B in its print orientation: rear wall on the bed, cup opening up.
module part_b_print() { translate([0, 0, case_len]) mirror([0, 0, 1]) part_b(); }

// =====================================================================
//  Evaluation views
// =====================================================================
module half_case() {                        // remove y > 0 (section view)
  difference() {
    case_only();
    translate([-100, 0, -100]) cube([200, 200, 200]);
  }
}
module half_driver() {
  difference() {
    driver_keepout();
    translate([-100, 0, -100]) cube([200, 200, 200]);
  }
}

// Net trapped air volume = case interior minus the driver keep-out.
// NOTE: the two bore segments must NOT overlap in z.  Part A's seat bore
// (r = seat_r) runs to z_joint_end; only after that does the wider part-B
// bore (r = ir_) begin, because in the lap region part A's spigot still
// occupies r = seat_r .. spigot_r.
module cavity() {
  difference() {
    union() {
      translate([0, 0, front_t]) cylinder(r = seat_r, h = z_joint_end - front_t);
      translate([0, 0, z_joint_end]) cylinder(r = ir_, h = z_int_end - z_joint_end);
    }
    driver_keepout();
    internal_bosses();
  }
}

// =====================================================================
//  Build
// =====================================================================
if      (part == "front")    part_a();
else if (part == "back")     part_b_print();
else if (part == "all")    { part_a();  translate([case_od + 8, 0, 0]) part_b_print(); }
else if (part == "assembly") case_only();
else if (part == "section")  half_case();
else if (part == "driver")   half_driver();
else if (part == "cavity")   cavity();
else if (part == "exploded") { part_a(); translate([0, 0, 8]) part_b(); }
else if (part == "driver_full") driver_keepout();
else if (part == "sec2d")     projection(cut = true) rotate([90, 0, 0]) case_only();
else if (part == "sec2d_drv") projection(cut = true) rotate([90, 0, 0]) driver_keepout();
else                         case_only();

// =====================================================================
//  Self-report:  volumes + sealed-box acoustic estimate
//  (printed by the CLI; also visible in the GUI console)
// =====================================================================
function cyl_v(r, h) = PI * r * r * h;
// area of a stadium (obround): rectangle plus two half-disc caps
function stadium_a(w, y_lo, y_hi) =
  w * (y_hi - y_lo - w) + PI * pow(w / 2, 2);

V1 = cyl_v(seat_r, drv_flange_h) - cyl_v(drv_od / 2, drv_flange_h);
V2 = cyl_v(seat_r, z_joint_end - front_t - drv_flange_h)
   - cyl_v(drv_body_d / 2, z_joint_end - front_t - drv_flange_h);
V3 = cyl_v(ir_, z_drv_back - z_joint_end) - cyl_v(drv_body_d / 2, z_drv_back - z_joint_end);
V4 = cyl_v(ir_, z_int_end - z_drv_back);
V_chamber = V1 + V2 + V3 + V4;                       // empty shell, before bosses
// Internal bosses displace air.  This sum ignores the small volume where the
// keyhole pad, well pad and cable boss overlap each other, so it slightly
// over-subtracts; the cavity MESH is the authoritative figure.
V_bosses = cyl_v(boss_or, boss_h)
         + (mount ? stadium_a(key_pad_w, key_pad_ylo, key_pad_yhi) * key_pad_h : 0)
         + cyl_v(well_pad_d / 2, well_pad_h);
Vb = V_chamber - V_bosses;                           // mm^3 of trapped air
Vb_cm3 = Vb / 1000;

// Sealed box:  alpha = Vas/Vb ;  fc = Fs*sqrt(1+alpha) ;  Qtc = Qts*sqrt(1+alpha)
function fc_of(fs, vas, vb)  = fs * sqrt(1 + vas / vb);
function qtc_of(qts, vas, vb) = qts * sqrt(1 + vas / vb);
// Normalised 2nd-order high-pass magnitude (1.0 == mid-band reference).
function resp(f, fc, qtc) =
  20 * log(
    pow(f / fc, 2)
    / sqrt(pow(1 - pow(f / fc, 2), 2) + pow((f / fc) / qtc, 2))
  );

// Plausible parameter sets for a 40 mm / 2 W / 8 ohm miniature driver.
// Fs is well documented for this class (400 Hz PUI AS04008MR-2, 580 Hz
// Visaton K 28.40 H); Vas/Qts almost never are, hence a sweep.
scenarios = [
  ["optimistic  ", 400, 0.80,  15],
  ["likely      ", 400, 0.90,  30],
  ["typical     ", 450, 1.00,  45],
  ["pessimistic ", 580, 1.10,  90],
  ["worst case  ", 580, 1.20, 200]
];

echo("================================================================");
echo(str(" case_len=", case_len, " mm  ->  outer ", case_od, " x ", case_len, " mm"));
echo(str(" gross interior      = ", V_chamber / 1000, " cm3"));
echo(str(" internal bosses     = ", V_bosses / 1000, " cm3"));
echo(str(" driver displacement = ", (cyl_v(drv_od/2, drv_flange_h)
      + cyl_v(drv_body_d/2, drv_depth - drv_flange_h)) / 1000, " cm3"));
echo(str(" NET AIR VOLUME Vb   = ", Vb_cm3, " cm3   (approx; cavity mesh is exact)"));
echo("----------------------------------------------------------------");
echo(str(" rear-wall floors:  keyhole=", key_floor, "  cable well=", well_floor,
         "  channel=", guide_floor, " (over pad) / ", guide_floor_bare, " (bare) mm"));
echo(str(" keyhole: entry=", key_entry_d, "  slot=", key_slot_w, "x", key_slot_len,
         "  lip=", key_lip, "mm retaining ", key_lip_w, "mm per side",
         "  cavity=", key_cav_w, "x", key_cav_h, "mm  (head travel ",
         key_y_hi - key_y_lo, "mm)"));
echo("----------------------------------------------------------------");
echo(" sealed-box estimate on Vb above:");
for (s = scenarios) {
  alpha = (s[3] * 1000) / Vb;                    // Vas in cm3 -> mm3
  fc_   = fc_of(s[1], s[3] * 1000, Vb);
  qtc_  = qtc_of(s[2], s[3] * 1000, Vb);
  echo(str("  ", s[0], " Fs=", s[1], " Hz  Qts=", s[2], "  Vas=", s[3],
           " cm3  ->  alpha=", alpha, "  Fc=", fc_, " Hz  Qtc=", qtc_));
}
echo("----------------------------------------------------------------");
echo(" predicted response of the 'likely' case (400 Hz / Qts 0.90 / Vas 30 cm3):");
for (f = [50, 100, 150, 200, 300, 400, 500, 700, 1000, 2000, 5000]) {
  alpha = 30000 / Vb;
  echo(str("   ", f, " Hz : ", resp(f, fc_of(400, 30000, Vb), qtc_of(0.90, 30000, Vb)), " dB"));
}
echo("================================================================");
