"""
Parametric enclosure for the 34 mm LED watch PCB + 35 mm round LiPo.

Assembly frame (FreeCAD):
    origin  = board centre
    +Y      = 12 o'clock / button side
    Z=0     = PCB back (solder) face,  Z=1.60 = PCB front face
    +Z      = out of the top (LED/bezel) side

Run headless:
    freecadcmd build_enclosure.py
"""
import json
import math
import os

import Draft
import FreeCAD as App
import MeshPart
import Part

# --------------------------------------------------------------------------
# Parameters (all mm)
# --------------------------------------------------------------------------
R_OUT = 20.0            # case outer radius (40 mm diameter)
R_PCB = 17.30           # bezel inner radius = 17.05 PCB + 0.25 radial clearance
R_BAT = 17.75           # battery cavity = 17.50 battery + 0.25 radial clearance
Z_SEAM = -0.20          # split line between bezel and bottom cover
Z_BOT = -9.10           # outside bottom
Z_FLOOR = -7.70         # inside floor of battery cavity
Z_TOP = 4.40            # outside top of bezel
Z_PLATE = 4.20          # underside of top plate
PCB_T = 1.60

BAT_R, BAT_T = 17.50, 5.50
BAT_TOP = -1.95
BAT_BOT = BAT_TOP - BAT_T           # -7.45
FOAM_T = 0.50
FOAM_TOP = BAT_TOP + FOAM_T         # -1.45
CUSHION_T = 0.25

RING_R0, RING_R1 = 13.05, 16.35    # light-pipe annulus (0.05 clearance each side)
RING_Z0, RING_Z1 = 2.70, 4.18      # 1.48 mm tall light pipe
LED_R = 14.30

# Dial markings: applied watch-style numerals at 12/3/6/9 + 8 index notches.
DIAL_R0, DIAL_R1 = 10.20, 13.00    # thickened dial land (avoids switch corner at r=9.96)
DIAL_Z0, DIAL_Z1 = 4.00, Z_TOP      # 0.40 mm land, supports a 0.15 mm engraving
NUM_R = 11.60
NUM_SIZE = 2.0
NUM_Z = 4.25                        # engraving floor
NOTCH_R = 12.10
NOTCH_LEN, NOTCH_W, NOTCH_DEPTH = 0.80, 0.22, 0.12
NOTCH_ANGLES = (30, 60, 120, 150, 210, 240, 300, 330)
DIAL_FONT = r"C:\Windows\Fonts\segoeuib.ttf"

BTN_XY = (0.0, 6.50)
BTN_HOLE_R = 2.70
BTN_STEM_R = 2.40
BTN_CAP_R = 3.50
BTN_SWITCH_TOP = 4.095
BTN_CAP_Z0, BTN_CAP_Z1 = 4.42, 5.15

# USB-C (measured from pcb.step: X +-4.47, Y -17.65..-10.35, Z -3.185..-0.085)
USBC_HW = 4.90
USBC_Y0, USBC_Y1 = -21.0, -14.00
USBC_ZBOT = -3.35

ANT_R0, ANT_R1 = 16.90, 19.10      # flex antenna annulus
ANT_Z0, ANT_Z1 = 3.87, 4.05
ANT_HALFSPAN = 40.0                # 80 deg arc (~28 mm at r=18)
KO_R0, KO_R1 = 16.60, 19.40
KO_Z0, KO_Z1 = 1.70, 3.85
POCKET_R0, POCKET_R1 = 16.75, 19.25
POCKET_Z0, POCKET_Z1 = 3.80, 4.10

HOOK_ANGLES = (45.0, 135.0, 225.0, 315.0)
LEDGE_ANGLES = (0.0, 120.0, 240.0)

OUT_DIR = r"C:\Users\juje\temporary\ongoing\adahack\led_watch\mech"
STL_DIR = os.path.join(OUT_DIR, "case")
REF_DIR = os.path.join(OUT_DIR, "reference")
os.makedirs(STL_DIR, exist_ok=True)
os.makedirs(REF_DIR, exist_ok=True)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def cyl(r, z0, z1, cx=0.0, cy=0.0):
    return Part.makeCylinder(r, z1 - z0, App.Vector(cx, cy, z0), App.Vector(0, 0, 1))


def box(x0, x1, y0, y1, z0, z1):
    return Part.makeBox(x1 - x0, y1 - y0, z1 - z0, App.Vector(x0, y0, z0))


def sector(r0, r1, a0, a1, z0, z1, step=1.0):
    """Annular sector solid, angles in degrees, measured from +X CCW."""
    n = max(2, int(math.ceil(abs(a1 - a0) / step)))
    outer, inner = [], []
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)
        outer.append(App.Vector(r1 * math.cos(a), r1 * math.sin(a)))
        inner.append(App.Vector(r0 * math.cos(a), r0 * math.sin(a)))
    pts = outer + inner[::-1]
    poly = Part.makePolygon(pts + [pts[0]])
    face = Part.Face(poly)
    return face.extrude(App.Vector(0, 0, z1 - z0)).translate(App.Vector(0, 0, z0))


def revolve(profile):
    """Revolve a closed (radius, z) polygon in the XZ plane about the Z axis."""
    pts = [App.Vector(r, 0.0, z) for r, z in profile]
    poly = Part.makePolygon(pts + [pts[0]])
    face = Part.Face(poly)
    return face.revolve(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 360)


def engrave_text(text, size, z_floor):
    """Return an extruded text solid centred on XY, ready to cut into a dial."""
    ss = Draft.makeShapeString(text, DIAL_FONT, size, 0.0)
    shp = ss.Shape
    bb = shp.BoundBox
    dx = -(bb.XMin + bb.XMax) / 2.0
    dy = -(bb.YMin + bb.YMax) / 2.0
    solids = [f.extrude(App.Vector(0, 0, 0.30)) for f in shp.Faces]
    tool = solids[0]
    for s in solids[1:]:
        tool = tool.fuse(s)
    tool.translate(App.Vector(dx, dy, z_floor))
    if App.ActiveDocument is not None:
        App.ActiveDocument.removeObject(ss.Name)
    return tool


def mesh_and_export(shape, path, deflection=0.015):
    mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=deflection,
                                  AngularDeflection=0.25, Relative=False)
    mesh.write(path)
    return mesh


# --------------------------------------------------------------------------
# Build the bottom cover
# --------------------------------------------------------------------------
def build_bottom():
    # outer cup, 0.8 mm bottom chamfer, 0.25 mm seam chamfer
    body = revolve([
        (0.00, Z_BOT), (19.20, Z_BOT), (20.00, -8.30),
        (20.00, -0.45), (19.78, Z_SEAM), (R_BAT, Z_SEAM),
        (R_BAT, Z_FLOOR), (0.00, Z_FLOOR),
    ])

    # three PCB support ledges (avoid the USB-C at 270 deg)
    for a in LEDGE_ANGLES:
        body = body.fuse(sector(16.40, R_PCB, a - 9, a + 9, -0.35, 0.0, 1.0))

    # snap-fit pockets in the inner wall
    for a in HOOK_ANGLES:
        body = body.cut(sector(R_BAT, R_BAT + 0.45, a - 7, a + 7, -1.38, -0.82, 1.0))

    # USB-C opening: through wall, open at the top rim
    body = body.cut(box(-USBC_HW, USBC_HW, USBC_Y0, USBC_Y1, USBC_ZBOT, Z_SEAM + 0.02))
    return body


# --------------------------------------------------------------------------
# Build the top bezel
# --------------------------------------------------------------------------
def build_top():
    # wall + 0.6 mm reinforced outer annulus + top chamfer
    body = revolve([
        (R_PCB, Z_SEAM), (19.78, Z_SEAM), (20.00, -0.42),
        (20.00, 3.70), (19.30, Z_TOP), (16.40, Z_TOP),
        (16.40, POCKET_Z0), (R_PCB, POCKET_Z0), (R_PCB, Z_SEAM),
    ])

    # inner top disc (the light ring window is the gap between r13 and r16.4)
    body = body.fuse(cyl(13.00, Z_PLATE, Z_TOP))

    # thicker dial land so the numerals/ticks can be engraved without breaking through
    dial_land = cyl(DIAL_R1, DIAL_Z0, DIAL_Z1).cut(
        cyl(DIAL_R0, DIAL_Z0 - 0.10, DIAL_Z1 + 0.10))
    body = body.fuse(dial_land)

    # button hole through the top disc
    body = body.cut(cyl(BTN_HOLE_R, Z_PLATE - 0.10, Z_TOP + 0.10, *BTN_XY))

    # applied-style engraved numerals at 12, 3, 6, 9 (all upright)
    for text, ang in (("12", 90.0), ("3", 0.0), ("6", 270.0), ("9", 180.0)):
        a = math.radians(ang)
        tool = engrave_text(text, NUM_SIZE, NUM_Z)
        tool.translate(App.Vector(NUM_R * math.cos(a), NUM_R * math.sin(a), 0.0))
        body = body.cut(tool)

    # eight small radial index notches at the in-between hours
    for ang in NOTCH_ANGLES:
        groove = box(-NOTCH_LEN / 2.0, NOTCH_LEN / 2.0,
                     -NOTCH_W / 2.0, NOTCH_W / 2.0,
                     Z_TOP - NOTCH_DEPTH, Z_TOP + 0.01)
        groove.translate(App.Vector(NOTCH_R, 0.0, 0.0))
        groove.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), ang)
        body = body.cut(groove)

    # antenna pockets under the top skin
    for c in (180.0, 0.0):
        body = body.cut(sector(POCKET_R0, POCKET_R1, c - ANT_HALFSPAN - 2.0,
                               c + ANT_HALFSPAN + 2.0, POCKET_Z0, POCKET_Z1, 1.0))

    # USB-C relief notch at the bottom of the bezel
    body = body.cut(box(-USBC_HW, USBC_HW, USBC_Y0, USBC_Y1, Z_SEAM - 0.02, 0.30))

    # four cantilever snap hooks on the inside
    for a in HOOK_ANGLES:
        body = body.fuse(sector(17.30, 17.70, a - 5, a + 5, -1.60, Z_SEAM + 0.001, 1.0))
        body = body.fuse(sector(17.70, 18.08, a - 4, a + 4, -1.30, -0.90, 1.0))

    # key-ring lug at 12 o'clock
    tab = box(-4.0, 4.0, 17.15, 24.0, 0.90, 3.10)
    tab = tab.fuse(cyl(4.0, 0.90, 3.10, 0.0, 24.0))
    tab = tab.cut(cyl(1.70, 0.50, 3.50, 0.0, 24.0))
    body = body.fuse(tab)
    return body


# --------------------------------------------------------------------------
# Other parts
# --------------------------------------------------------------------------
def build_light_ring():
    return revolve([
        (RING_R0, RING_Z0), (RING_R1, RING_Z0),
        (RING_R1, RING_Z1 - 0.28), (RING_R1 - 0.28, RING_Z1),
        (RING_R0 + 0.20, RING_Z1), (RING_R0, RING_Z1 - 0.20),
    ])


def build_button():
    btn = revolve([
        (0.00, BTN_SWITCH_TOP - 0.01), (BTN_STEM_R, BTN_SWITCH_TOP - 0.01),
        (BTN_STEM_R, BTN_CAP_Z0), (3.30, BTN_CAP_Z0), (BTN_CAP_R, BTN_CAP_Z0 + 0.20),
        (BTN_CAP_R, BTN_CAP_Z1 - 0.20), (3.20, BTN_CAP_Z1), (0.00, BTN_CAP_Z1),
    ])
    btn.translate(App.Vector(*BTN_XY, 0.0))
    # shallow concave thumb dish
    dish = Part.makeSphere(10.0).translate(
        App.Vector(BTN_XY[0], BTN_XY[1], BTN_CAP_Z1 + 10.0 - 0.30))
    return btn.cut(dish)


def build_battery():
    bat = revolve([
        (0.00, BAT_BOT), (16.95, BAT_BOT), (BAT_R, BAT_BOT + 0.55),
        (BAT_R, BAT_TOP - 0.55), (16.95, BAT_TOP), (0.00, BAT_TOP),
    ])
    notch = box(-6.0, 6.0, -18.0, -9.80, BAT_TOP - 1.50, BAT_TOP + 0.01)
    return bat.cut(notch)


def build_foam():
    pad = cyl(17.20, BAT_TOP, FOAM_TOP)
    return pad.cut(box(-6.2, 6.2, -18.0, -9.50, BAT_TOP - 0.1, FOAM_TOP + 0.1))


def build_cushion():
    return cyl(17.0, Z_FLOOR, BAT_BOT + 0.001)


def build_antenna(center_deg):
    return sector(ANT_R0, ANT_R1, center_deg - ANT_HALFSPAN, center_deg + ANT_HALFSPAN,
                  ANT_Z0, ANT_Z1, 1.0)


def build_keepout(center_deg):
    return sector(KO_R0, KO_R1, center_deg - ANT_HALFSPAN - 3.0,
                  center_deg + ANT_HALFSPAN + 3.0, KO_Z0, KO_Z1, 1.0)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    doc = App.newDocument("led_watch_enclosure")
    print("building case parts ...")
    top = build_top()
    bottom = build_bottom()
    ring = build_light_ring()
    button = build_button()
    battery = build_battery()
    foam = build_foam()
    cushion = build_cushion()
    ant_lte = build_antenna(180.0)
    ant_gnss = build_antenna(0.0)
    ko_lte = build_keepout(180.0)
    ko_gnss = build_keepout(0.0)

    # ---- load PCB assembly ------------------------------------------------
    print("loading pcb.step ...")
    pcb = Part.Shape()
    pcb.read(os.path.join(OUT_DIR, "exports", "pcb.step"))
    pcb.translate(App.Vector(-100.0, 100.0, 0.0))
    bb = pcb.BoundBox
    print("pcb bbox: X %.2f..%.2f Y %.2f..%.2f Z %.3f..%.3f" % (
        bb.XMin, bb.XMax, bb.YMin, bb.YMax, bb.ZMin, bb.ZMax))

    # ---- interference checks ---------------------------------------------
    print("interference checks ...")
    checks = {}

    def common_vol(a, b):
        try:
            c = a.common(b)
            return round(c.Volume, 4), len(c.Solids)
        except Exception as exc:
            return None, "ERR %s" % exc

    checks["case_top_vs_pcb"] = common_vol(top, pcb)
    checks["case_bottom_vs_pcb"] = common_vol(bottom, pcb)
    checks["case_top_vs_battery"] = common_vol(top, battery)
    checks["case_bottom_vs_battery"] = common_vol(bottom, battery)
    checks["pcb_vs_battery"] = common_vol(pcb, battery)
    checks["pcb_vs_foam"] = common_vol(pcb, foam)
    checks["battery_vs_foam"] = common_vol(battery, foam)
    checks["pcb_vs_keepout_lte"] = common_vol(pcb, ko_lte)
    checks["pcb_vs_keepout_gnss"] = common_vol(pcb, ko_gnss)
    checks["battery_vs_keepout_lte"] = common_vol(battery, ko_lte)
    checks["battery_vs_keepout_gnss"] = common_vol(battery, ko_gnss)
    checks["case_top_vs_ring"] = common_vol(top, ring)
    checks["case_bottom_vs_ring"] = common_vol(bottom, ring)
    checks["case_top_vs_button"] = common_vol(top, button)
    checks["ring_vs_button"] = common_vol(ring, button)

    for k, v in checks.items():
        print("  %-32s volume=%s solids=%s" % (k, v[0], v[1]))

    # ---- dimensions -------------------------------------------------------
    def dims(name, shape):
        b = shape.BoundBox
        print("  %-14s %.2f x %.2f x %.2f mm" % (name, b.XLength, b.YLength, b.ZLength))
        return [round(b.XLength, 3), round(b.YLength, 3), round(b.ZLength, 3)]

    print("part envelopes:")
    report = {
        "frame": "board centre origin, PCB back face Z=0, +Y = button side",
        "parts_mm": {},
        "interference_mm3": {k: v[0] for k, v in checks.items()},
        "interference_solid_count": {k: v[1] for k, v in checks.items()},
    }
    for name, shp in [("case_top", top), ("case_bottom", bottom), ("button", button),
                      ("light_ring", ring), ("battery", battery), ("foam_pad", foam),
                      ("antenna_lte", ant_lte), ("antenna_gnss", ant_gnss),
                      ("keepout_lte", ko_lte), ("keepout_gnss", ko_gnss)]:
        report["parts_mm"][name] = dims(name, shp)
    report["enclosure_outer_mm"] = [40.0, 48.0, round(Z_TOP - Z_BOT, 3)]
    report["battery_volume_mm3"] = round(battery.Volume, 1)
    report["battery_nominal_mm3"] = round(math.pi * BAT_R ** 2 * BAT_T, 1)
    report["dial_markings"] = {
        "numerals": ["12", "3", "6", "9"],
        "numeral_radius_mm": NUM_R,
        "numeral_cap_height_mm": NUM_SIZE,
        "engrave_depth_mm": round(Z_TOP - NUM_Z, 2),
        "index_notches": len(NOTCH_ANGLES),
        "index_notch_angles_deg": list(NOTCH_ANGLES),
        "index_notch_depth_mm": NOTCH_DEPTH,
    }

    # ---- save FreeCAD document -------------------------------------------
    for name, shp in [("Case_Top", top), ("Case_Bottom", bottom), ("Button", button),
                      ("Light_Ring", ring), ("Battery_LPR553535", battery),
                      ("Foam_Pad_0.5mm", foam), ("Battery_Cushion", cushion),
                      ("Antenna_LTE", ant_lte), ("Antenna_GNSS", ant_gnss),
                      ("Keepout_LTE", ko_lte), ("Keepout_GNSS", ko_gnss),
                      ("PCB_Assembly", pcb)]:
        o = doc.addObject("Part::Feature", name)
        o.Shape = shp
    doc.recompute()
    fcstd = os.path.join(OUT_DIR, "led_watch_enclosure.FCStd")
    doc.saveAs(fcstd)
    print("saved", fcstd)

    # ---- STL exports ------------------------------------------------------
    print("exporting STL (deflection 0.015 mm) ...")
    exports = [
        (top, os.path.join(STL_DIR, "case_top.stl")),
        (bottom, os.path.join(STL_DIR, "case_bottom.stl")),
        (button, os.path.join(STL_DIR, "button.stl")),
        (ring, os.path.join(STL_DIR, "light_ring.stl")),
        (battery, os.path.join(REF_DIR, "battery.stl")),
        (foam, os.path.join(REF_DIR, "foam_pad.stl")),
        (ant_lte, os.path.join(REF_DIR, "antenna_lte.stl")),
        (ant_gnss, os.path.join(REF_DIR, "antenna_gnss.stl")),
        (ko_lte, os.path.join(REF_DIR, "keepout_lte.stl")),
        (ko_gnss, os.path.join(REF_DIR, "keepout_gnss.stl")),
    ]
    for shp, path in exports:
        m = mesh_and_export(shp, path)
        print("  %-42s %6d tris" % (os.path.basename(path), len(m.Facets)))

    with open(os.path.join(OUT_DIR, "verification.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("wrote verification.json")
    print("done")


main()
