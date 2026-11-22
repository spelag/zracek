"""Generate a clean, dimensioned A3 technical sheet (PNG + SVG) from the
same parameters used to build the enclosure.  Drawing units are mm on an
A3 landscape sheet; the views are drawn at 2:1."""
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import patches
from matplotlib.font_manager import FontProperties

OUT = "/mnt/c/Users/juje/temporary/ongoing/adahack/led_watch/mech/render"
os.makedirs(OUT, exist_ok=True)

# --- dimensions (mm) ------------------------------------------------------
S = 2.0
R = 20.0
Z_TOP, Z_SEAM, Z_BOT = 4.40, -0.20, -9.10
BODY_H = Z_TOP - Z_BOT
BTN = (0.0, 6.50)
BTN_R = 3.50
RING_R0, RING_R1 = 13.05, 16.35
LUG_HW = 4.0
LUG_Y0, LUG_Y1 = 19.60, 24.0
HOLE_R = 1.70
LUG_TIP = 28.0
USBC_W, USBC_Z0, USBC_Z1 = 9.80, -3.35, 0.30
DIAL_R = 11.60
NOTCH_R = 12.10
NOTCH_ANGLES = (30, 60, 120, 150, 210, 240, 300, 330)
BAT_R = 17.50

INK = "#12171c"
DIM = "#28506e"
HID = "#8a9197"
THIN = "#b8bec3"
FILL = "#eef0f1"
TEAL = "#2f8f7b"
SKIN = "#d8c3ae"

FONT = "/mnt/c/Windows/Fonts/segoeui.ttf"
FONT_B = "/mnt/c/Windows/Fonts/segoeuib.ttf"
fp = FontProperties(fname=FONT)
fpb = FontProperties(fname=FONT_B)

fig = plt.figure(figsize=(16.54, 11.69))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 420)
ax.set_ylim(0, 297)
ax.set_aspect("equal")
ax.axis("off")
ax.set_facecolor("white")
fig.patch.set_facecolor("white")


def line(x1, y1, x2, y2, lw=0.8, color=INK, ls="-", z=2):
    ax.plot([x1, x2], [y1, y2], lw=lw, color=color, linestyle=ls,
            solid_capstyle="round", zorder=z)


def poly(pts, lw=0.8, color=INK, fill=None, fc="none", ls="-", z=2, hatch=None):
    p = patches.Polygon(pts, closed=True, fill=(fill is not None),
                        facecolor=fc if fill else "none", edgecolor=color,
                        linewidth=lw, linestyle=ls, zorder=z, hatch=hatch)
    ax.add_patch(p)
    return p


def circ(cx, cy, r, lw=0.8, color=INK, fill=False, fc="white", ls="-", z=2):
    p = patches.Circle((cx, cy), r, fill=fill, facecolor=fc, edgecolor=color,
                       linewidth=lw, linestyle=ls, zorder=z)
    ax.add_patch(p)
    return p


def arc(cx, cy, r, a0, a1, lw=0.8, color=INK, ls="-", z=2):
    p = patches.Arc((cx, cy), 2 * r, 2 * r, theta1=a0, theta2=a1,
                    edgecolor=color, linewidth=lw, linestyle=ls, zorder=z)
    ax.add_patch(p)
    return p


def txt(x, y, s, size=6.0, color=INK, ha="center", va="center", rot=0, bold=False, z=6):
    ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va, rotation=rot,
            fontproperties=fpb if bold else fp, zorder=z)


def arrow(x1, y1, x2, y2, lw=0.55, color=DIM):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="<|-|>", mutation_scale=5.0,
                                lw=lw, color=color, shrinkA=0, shrinkB=0),
                zorder=4)


def dim_h(x1, x2, y, label, y_feat, ext=2.5, size=5.8, above=True):
    for x in (x1, x2):
        line(x, y_feat, x, y + (ext if above else -ext), lw=0.35, color=THIN)
    arrow(x1, y, x2, y)
    txt((x1 + x2) / 2, y + (1.8 if above else -1.8), label, size=size, color=DIM)


def dim_v(y1, y2, x, label, x_feat, ext=2.5, size=5.8, right=True):
    for y in (y1, y2):
        line(x_feat, y, x + (ext if right else -ext), y, lw=0.35, color=THIN)
    arrow(x, y1, x, y2)
    txt(x + (1.8 if right else -1.8), (y1 + y2) / 2, label, size=size,
        color=DIM, rot=90)


def leader(px, py, bend, end, label, ha="left", size=5.6, color=DIM):
    bx, by = bend
    ex, ey = end
    line(px, py, bx, by, lw=0.5, color=color)
    line(bx, by, ex, ey, lw=0.5, color=color)
    ax.annotate("", xy=(px, py), xytext=(bx, by),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=4.5,
                                lw=0.5, color=color, shrinkA=0, shrinkB=0), zorder=4)
    txt(ex + (1.5 if ha == "left" else -1.5), ey, label, size=size,
        color=color, ha=ha, va="center")


def centerline(x1, y1, x2, y2):
    line(x1, y1, x2, y2, lw=0.4, color=HID, ls=(0, (7, 2, 1, 2)), z=1)


# ==========================================================================
# TOP VIEW  (centre 95,195)
# ==========================================================================
CX, CY = 95.0, 195.0


def TX(mx):
    return CX + mx * S


def TY(my):
    return CY + my * S


circ(CX, CY, R * S, lw=1.0)
circ(CX, CY, RING_R1 * S, lw=0.6, color=DIM)
circ(CX, CY, RING_R0 * S, lw=0.6, color=DIM)
# dial numerals + ticks
for ang in NOTCH_ANGLES:
    a = math.radians(ang)
    line(TX(11.70 * math.cos(a)), TY(11.70 * math.sin(a)),
         TX(12.50 * math.cos(a)), TY(12.50 * math.sin(a)), lw=0.9)
for text, ang in (("12", 90), ("3", 0), ("6", 270), ("9", 180)):
    a = math.radians(ang)
    txt(TX(DIAL_R * math.cos(a)), TY(DIAL_R * math.sin(a)), text, size=8.5, bold=True)
# button
circ(TX(BTN[0]), TY(BTN[1]), BTN_R * S, lw=0.8)
circ(TX(BTN[0]), TY(BTN[1]), 2.40 * S, lw=0.45, color=DIM)
# lug
line(TX(-LUG_HW), TY(LUG_Y0), TX(-LUG_HW), TY(LUG_Y1), lw=1.0)
line(TX(LUG_HW), TY(LUG_Y0), TX(LUG_HW), TY(LUG_Y1), lw=1.0)
arc(TX(0), TY(LUG_Y1), LUG_HW * S, 0, 180, lw=1.0)
circ(TX(0), TY(LUG_Y1), HOLE_R * S, lw=0.8)
centerline(CX - R * S - 6, CY, CX + R * S + 6, CY)
centerline(CX, CY - R * S - 6, CX, CY + R * S + 8)

# diameter
leader(TX(-R * 0.707), TY(R * 0.707), (CX - 42, CY + 42), (CX - 54, CY + 42),
       "Ø40.0", ha="right")
# overall length 48
dim_v(TY(-R), TY(LUG_TIP), CX + R * S + 14, "48.0", CX + R * S, size=6.0)
# lug width
dim_h(TX(-LUG_HW), TX(LUG_HW), TY(LUG_TIP) + 8, "8.0", TY(LUG_Y1) + 4, size=6.0)
# hole
leader(TX(0), TY(LUG_Y1) + HOLE_R * S * 0.7, (CX + 24, CY + 56),
       (CX + 40, CY + 56), "Ø3.4 THRU")
# light ring
leader(TX(RING_R1 * 0.707), TY(RING_R1 * 0.707), (CX + 32, CY + 30),
       (CX + 44, CY + 30), "LIGHT RING Ø32.7 / Ø26.1")
# button
leader(TX(BTN[0] + 2.4), TY(BTN[1] + 2.4), (CX + 30, CY + 18),
       (CX + 42, CY + 18), "BUTTON Ø7.0")
dim_v(TY(0), TY(BTN[1]), CX - R * S - 10, "6.5", CX - R * S, size=5.8)
# dial note
leader(TX(-8.5), TY(8.5), (CX - 34, CY + 70), (CX - 52, CY + 70),
       "12/3/6/9 ENGRAVED 0.15", ha="right")
txt(CX, CY - R * S - 15, "TOP VIEW", size=7.5, bold=True)
txt(CX, CY - R * S - 21, "SCALE 2:1", size=6.0, color=DIM)

# ==========================================================================
# FRONT VIEW (USB-C side)  centre 255,205
# ==========================================================================
FX, FY = 255.0, 205.0


def FXg(mx):
    return FX + mx * S


def FYg(mz):
    return FY + mz * S


# body silhouette with chamfers
body = [
    (FXg(-19.2), FYg(Z_BOT)), (FXg(19.2), FYg(Z_BOT)),
    (FXg(20.0), FYg(-8.30)), (FXg(20.0), FYg(-0.45)),
    (FXg(19.78), FYg(Z_SEAM)),
    (FXg(20.0), FYg(-0.42)), (FXg(20.0), FYg(3.70)),
    (FXg(19.30), FYg(Z_TOP)), (FXg(-19.30), FYg(Z_TOP)),
    (FXg(-20.0), FYg(3.70)), (FXg(-20.0), FYg(-0.42)),
    (FXg(-19.78), FYg(Z_SEAM)), (FXg(-20.0), FYg(-0.45)),
    (FXg(-20.0), FYg(-8.30)), (FXg(-19.2), FYg(Z_BOT)),
]
poly(body, lw=1.0)
# seam
line(FXg(-19.78), FYg(Z_SEAM), FXg(19.78), FYg(Z_SEAM), lw=0.45, color=HID, ls=(0, (5, 2)))
# button
btn = [(FXg(-3.30), FYg(4.42)), (FXg(-3.50), FYg(4.62)), (FXg(-3.50), FYg(4.95)),
       (FXg(-3.20), FYg(5.15)), (FXg(3.20), FYg(5.15)), (FXg(3.50), FYg(4.95)),
       (FXg(3.50), FYg(4.62)), (FXg(3.30), FYg(4.42))]
poly(btn, lw=0.8, fill=True, fc="#f7f7f7")
# USB-C slot
poly([(FXg(-USBC_W / 2), FYg(USBC_Z0)), (FXg(USBC_W / 2), FYg(USBC_Z0)),
      (FXg(USBC_W / 2), FYg(USBC_Z1)), (FXg(-USBC_W / 2), FYg(USBC_Z1))],
     lw=0.9, fill=True, fc="#1b1f23")
centerline(FXg(0), FYg(4.4) - 10, FXg(0), FYg(5.15) + 8)

dim_h(FXg(-20), FXg(20), FYg(Z_BOT) - 12, "40.0", FYg(Z_BOT), size=6.2)
dim_v(FYg(Z_BOT), FYg(Z_TOP), FXg(-20) - 14, "13.5", FXg(-20), size=6.2)
dim_v(FYg(Z_BOT), FYg(5.15), FXg(20) + 14, "14.25", FXg(20), size=6.2)
# seam from top
leader(FXg(-10.0), FYg(Z_SEAM), (FXg(-22.0), FYg(1.8)),
       (FXg(-30.0), FYg(1.8)), "SEAM 4.6 BELOW TOP", ha="right", size=5.0)
# USB-C dims
dim_h(FXg(-USBC_W / 2), FXg(USBC_W / 2), FYg(USBC_Z0) - 7, "9.8", FYg(USBC_Z0), size=5.6)
dim_v(FYg(USBC_Z0), FYg(USBC_Z1), FXg(USBC_W / 2) + 7, "3.65", FXg(USBC_W / 2), size=5.6)
dim_v(FYg(Z_BOT), FYg(USBC_Z0), FXg(-USBC_W / 2) - 8, "5.75", FXg(-USBC_W / 2), size=5.4, right=False)
leader(FXg(3.0), FYg(-1.5), (FXg(14), FYg(-6.5)), (FXg(20), FYg(-6.5)), "USB-C", size=5.6)
leader(FXg(18.5), FYg(4.1), (FXg(24), FYg(9.5)), (FXg(30), FYg(9.5)), "0.7 × 45°", size=5.2)
leader(FXg(18.8), FYg(-8.7), (FXg(24), FYg(-13.5)), (FXg(30), FYg(-13.5)), "0.8 × 45°", size=5.2)
leader(FXg(0), FYg(5.15), (FXg(12), FYg(10.5)), (FXg(18), FYg(10.5)), "BUTTON 0.75 HIGH", size=5.2)
txt(FX, FYg(Z_BOT) - 24, "FRONT VIEW (USB-C SIDE)", size=7.5, bold=True)
txt(FX, FYg(Z_BOT) - 30, "SCALE 2:1", size=6.0, color=DIM)

# ==========================================================================
# RIGHT SIDE VIEW  centre 255,130
# ==========================================================================
SX, SY = 255.0, 130.0


def SXg(my):
    return SX + my * S


def SYg(mz):
    return SY + mz * S


side = [
    (SXg(-19.2), SYg(Z_BOT)), (SXg(19.2), SYg(Z_BOT)),
    (SXg(20.0), SYg(-8.30)), (SXg(20.0), SYg(-0.45)),
    (SXg(19.78), SYg(Z_SEAM)),
    (SXg(20.0), SYg(-0.42)), (SXg(20.0), SYg(3.70)),
    (SXg(19.30), SYg(Z_TOP)), (SXg(-19.30), SYg(Z_TOP)),
    (SXg(-20.0), SYg(3.70)), (SXg(-20.0), SYg(-0.42)),
    (SXg(-19.78), SYg(Z_SEAM)), (SXg(-20.0), SYg(-0.45)),
    (SXg(-20.0), SYg(-8.30)), (SXg(-19.2), SYg(Z_BOT)),
]
poly(side, lw=1.0)
line(SXg(-19.78), SYg(Z_SEAM), SXg(19.78), SYg(Z_SEAM), lw=0.45, color=HID, ls=(0, (5, 2)))
# button profile on top (cap spans y 3..10 mm)
poly([(SXg(3.0), SYg(4.42)), (SXg(3.0), SYg(4.95)), (SXg(3.2), SYg(5.15)),
      (SXg(9.8), SYg(5.15)), (SXg(10.0), SYg(4.95)), (SXg(10.0), SYg(4.42))],
     lw=0.8, fill=True, fc="#f7f7f7")
# lug side profile
poly([(SXg(20.0), SYg(0.90)), (SXg(28.0), SYg(0.90)),
      (SXg(28.0), SYg(3.10)), (SXg(20.0), SYg(3.10))], lw=0.9, fill=True, fc="#f7f7f7")
# hidden hole
line(SXg(24.0 - HOLE_R), SYg(0.90), SXg(24.0 - HOLE_R), SYg(3.10), lw=0.45, color=HID, ls=(0, (4, 2)))
line(SXg(24.0 + HOLE_R), SYg(0.90), SXg(24.0 + HOLE_R), SYg(3.10), lw=0.45, color=HID, ls=(0, (4, 2)))
centerline(SXg(24.0), SYg(0.9) - 4, SXg(24.0), SYg(3.1) + 4)

dim_h(SXg(-20), SXg(28), SYg(Z_TOP) + 16, "48.0", SYg(Z_TOP), size=6.2)
dim_h(SXg(20), SXg(28), SYg(Z_TOP) + 8, "8.0", SYg(Z_TOP), size=5.8)
dim_v(SYg(0.90), SYg(3.10), SXg(28.0) + 10, "2.2", SXg(28.0), size=5.6)
dim_v(SYg(Z_BOT), SYg(Z_TOP), SXg(-20.0) - 14, "13.5", SXg(-20.0), size=6.0)
leader(SXg(24.0), SYg(3.1), (SXg(24.0) + 8, SYg(3.1) + 9),
       (SXg(24.0) + 16, SYg(3.1) + 9), "Ø3.4 THRU (HIDDEN)", size=5.2)
leader(SXg(26.0), SYg(0.9), (SXg(26.0) + 8, SYg(0.9) - 10),
       (SXg(26.0) + 16, SYg(0.9) - 10), "LUG 8.0 WIDE", size=5.2)
txt(SX, SYg(Z_BOT) - 24, "RIGHT SIDE VIEW", size=7.5, bold=True)
txt(SX, SYg(Z_BOT) - 30, "SCALE 2:1", size=6.0, color=DIM)

# ==========================================================================
# BOTTOM VIEW  centre 95,75
# ==========================================================================
BX, BY = 95.0, 75.0


def BXg(mx):
    return BX + mx * S


def BYg(my):
    return BY + my * S


circ(BX, BY, R * S, lw=1.0)
circ(BX, BY, BAT_R * S + 0.5, lw=0.45, color=HID, ls=(0, (6, 2, 1, 2)))
# lug
line(BXg(-LUG_HW), BYg(LUG_Y0), BXg(-LUG_HW), BYg(LUG_Y1), lw=1.0)
line(BXg(LUG_HW), BYg(LUG_Y0), BXg(LUG_HW), BYg(LUG_Y1), lw=1.0)
arc(BXg(0), BYg(LUG_Y1), LUG_HW * S, 0, 180, lw=1.0)
circ(BXg(0), BYg(LUG_Y1), HOLE_R * S, lw=0.8)
# USB-C notch in the wall
poly([(BXg(-USBC_W / 2), BYg(-R)), (BXg(USBC_W / 2), BYg(-R)),
      (BXg(USBC_W / 2), BYg(-17.75)), (BXg(-USBC_W / 2), BYg(-17.75))],
     lw=0.9, fill=True, fc="white")
centerline(BX - R * S - 6, BY, BX + R * S + 6, BY)
centerline(BX, BY - R * S - 6, BX, BY + R * S + 8)
leader(BXg(-R * 0.707), BYg(-R * 0.707), (BX - 38, BY - 38), (BX - 50, BY - 38),
       "Ø40.0", ha="right")
dim_h(BXg(-USBC_W / 2), BXg(USBC_W / 2), BYg(-R) - 10, "9.8", BYg(-R), size=5.8)
leader(BXg(0), BYg(-18.5), (BX + 20, BY - 30), (BX + 34, BY - 30),
       "USB-C CUTOUT 9.8 WIDE", size=5.2)
leader(BXg(-BAT_R * 0.707), BYg(BAT_R * 0.707), (BX - 38, BY + 30), (BX - 50, BY + 30),
       "BATTERY CAVITY Ø35.5 (INT.)", ha="right", size=5.2)
txt(BX, BY - R * S - 18, "BOTTOM VIEW", size=7.5, bold=True)
txt(BX, BY - R * S - 24, "SCALE 2:1", size=6.0, color=DIM)

# ==========================================================================
# STACK-UP DETAIL  (schematic, x 350..382, y 120..174)
# ==========================================================================
UX, UY0 = 350.0, 118.0
USS = 4.0
bands = [
    (-9.10, -7.70, "BOTTOM 1.4", "#dfe2e4"),
    (-7.70, -7.45, "CUSHION 0.25", "#c9cdd0"),
    (-7.45, -1.95, "LiPo Ø35 × 5.5", "#b7c3cc"),
    (-1.95, -1.45, "FOAM 0.5", "#4a4f53"),
    (-1.45, 0.00, "GAP 1.45", "#f3f3f3"),
    (0.00, 1.60, "PCB Ø34.1 × 1.6", "#9fb9a8"),
    (1.60, 2.70, "CAVITY 1.1", "#f3f3f3"),
    (2.70, 4.18, "RING 1.48", "#8fd4c5"),
    (4.18, 4.40, "PLATE 0.22", "#dfe2e4"),
]


def UY(z):
    return UY0 + (z - Z_BOT) * USS


for z0, z1, label, color in bands:
    y0, y1 = UY(z0), UY(z1)
    ax.add_patch(patches.Rectangle((UX, y0), 32, max(y1 - y0, 0.35),
                                   facecolor=color, edgecolor=INK, lw=0.5, zorder=2))
    txt(UX + 34, (y0 + y1) / 2, label, size=4.4, ha="left", color=INK)
# overall height
arrow(UX - 5, UY(Z_BOT), UX - 5, UY(4.40))
line(UX - 7, UY(Z_BOT), UX - 3, UY(Z_BOT), lw=0.4, color=DIM)
line(UX - 7, UY(4.40), UX - 3, UY(4.40), lw=0.4, color=DIM)
txt(UX - 7, (UY(Z_BOT) + UY(4.40)) / 2, "13.5", size=4.6, color=DIM, rot=90)
# PCB and battery references
line(UX + 32, UY(-7.45), UX + 46, UY(-7.45), lw=0.35, color=THIN)
line(UX + 32, UY(0.0), UX + 46, UY(0.0), lw=0.35, color=THIN)
txt(UX + 16, UY(5.15) + 6, "STACK-UP (SCHEMATIC)", size=5.0, bold=True)
txt(UX + 16, UY(5.15) + 2.2, "Z FROM PCB BACK FACE", size=4.0, color=DIM)

# ==========================================================================
# TITLE BLOCK + NOTES
# ==========================================================================
TBX, TBY, TBW, TBH = 300, 10, 110, 46
ax.add_patch(patches.Rectangle((TBX, TBY), TBW, TBH, fill=False, edgecolor=INK, lw=0.9, zorder=5))
line(TBX, TBY + 30, TBX + TBW, TBY + 30, lw=0.7)
line(TBX + 62, TBY + 30, TBX + 62, TBY + TBH, lw=0.7)
line(TBX, TBY + 16, TBX + TBW, TBY + 16, lw=0.5)
line(TBX + 34, TBY, TBX + 34, TBY + 16, lw=0.5)
line(TBX + 62, TBY, TBX + 62, TBY + 16, lw=0.5)
line(TBX + 86, TBY, TBX + 86, TBY + 16, lw=0.5)
txt(TBX + 4, TBY + 40, "LED WATCH — ENCLOSURE", size=8.0, ha="left", bold=True)
txt(TBX + 4, TBY + 35, "TWO-PIECE BEZEL + SNAP-FIT BOTTOM COVER", size=4.6, ha="left", color=DIM)
txt(TBX + 4, TBY + 24, "SCALE", size=4.2, ha="left", color=DIM)
txt(TBX + 4, TBY + 20, "2:1", size=6.0, ha="left", bold=True)
txt(TBX + 38, TBY + 24, "UNITS", size=4.2, ha="left", color=DIM)
txt(TBX + 38, TBY + 20, "mm", size=6.0, ha="left", bold=True)
txt(TBX + 66, TBY + 24, "THIRD ANGLE", size=4.2, ha="left", color=DIM)
# third-angle symbol
line(TBX + 93, TBY + 22, TBX + 103, TBY + 22, lw=0.4)
line(TBX + 93, TBY + 20, TBX + 103, TBY + 20, lw=0.4)
arc(TBX + 98, TBY + 21, 4.0, 0, 360, lw=0.4)
txt(TBX + 4, TBY + 10, "PROJECT", size=4.2, ha="left", color=DIM)
txt(TBX + 4, TBY + 5.5, "adahack / led_watch", size=4.6, ha="left")
txt(TBX + 38, TBY + 10, "DATE", size=4.2, ha="left", color=DIM)
txt(TBX + 38, TBY + 5.5, "2026-11-22", size=4.6, ha="left")
txt(TBX + 66, TBY + 10, "SHEET", size=4.2, ha="left", color=DIM)
txt(TBX + 66, TBY + 5.5, "1 / 1", size=4.6, ha="left")
txt(TBX + 90, TBY + 10, "REV", size=4.2, ha="left", color=DIM)
txt(TBX + 90, TBY + 5.5, "A", size=4.6, ha="left")

# notes block
NX, NY = 150, 24
txt(NX, NY + 34, "NOTES", size=6.0, ha="left", bold=True)
notes = [
    "1. ALL DIMENSIONS IN mm. GENERAL TOLERANCE ±0.15.",
    "2. 0.25 RADIAL CLEARANCE TO PCB (Ø34.1) AND BATTERY (Ø35.0).",
    "3. 4 × SNAP HOOKS AT 45°/135°/225°/315°; 3 × PCB LEDGES.",
    "4. LIGHT RING 1.48 THK TRANSLUCENT PC, 0.22 BELOW TOP FACE.",
    "5. 12/3/6/9 ENGRAVED 0.15 DEEP; 8 INDEX NOTCHES 0.12 DEEP.",
    "6. ANTENNA FLEXES 0.18 THK IN BEZEL POCKETS; KEEP-OUTS R16.6–19.4.",
]
for i, n in enumerate(notes):
    txt(NX, NY + 28 - i * 4.2, n, size=4.6, ha="left", color=INK)

# sheet border
ax.add_patch(patches.Rectangle((8, 8), 404, 281, fill=False, edgecolor=INK, lw=1.1, zorder=5))

png = os.path.join(OUT, "technical_drawing.png")
svg = os.path.join(OUT, "technical_drawing.svg")
fig.savefig(png, dpi=200, facecolor="white")
fig.savefig(svg, facecolor="white")
print("wrote", png)
print("wrote", svg)
