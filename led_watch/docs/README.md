# LED Watch — 34 mm round PCB, v2 (compacted)

Same circuit as v1, re-packed for a **35 × 5.5 mm round battery** and a smart-watch
enclosure.

- Board: **⌀34 mm** (17 mm radius), 4 layers, 1.6 mm FR-4, ENIG
- 12 × WS2812B-2020 on a **14.3 mm radius ring** (30° apart)
- nRF9151-LACA in the middle-bottom, tactile button above it
- eSIM moved to the **back**, directly under the SiP
- Fully autorouted: 0 unrouted nets

## What changed from the 50 mm version

| | v1 (⌀50) | v2 (⌀34) |
|---|---|---|
| Board | 50 mm | **34 mm** |
| LED ring radius | 21.5 mm | **14.3 mm** |
| SWD | 1×5 1.27 mm through-hole header | **5 SMD test pads** (pogo only, 0.05 mm tall) |
| Battery | JST-PH SMD connector (5 mm tall) | **2 solder pads** (wires soldered directly) |
| eSIM | front, right of the SiP | **back, centre, under the SiP** |
| Bulk caps C1/C2/C4 | front | **back** |
| LED decoupling | front, in the ring gaps | **back, under each LED** (8) + 4 on the front inner radius |
| Button position | dead centre | **6 mm above centre** (see note) |

**Button note:** the nRF9151 is 12 × 11 mm and the board is 34 mm, so a 6 × 6 mm
button cannot sit at the geometric centre. It is at (100, 93.5) — 6 mm above
centre, immediately in front of the SiP — which is where a pusher would be on a
watch dial anyway. If you need it exactly centred, the only way is a smaller
switch (< 4 mm) and a wider board.

## Back-side height budget

Everything on the back is low except the connectors, which sit at the board
edge and overhang it:

| Part | Height above B.Cu |
|---|---|
| MFF2 eSIM, 0402/0603 parts | ~0.6 mm |
| Test pads / battery pads | ~0.05 mm |
| U.FL connectors (J2/J3) | 1.9 mm |
| USB-C (J1) | 3.1 mm — overhangs the board edge by ~1.4 mm |

Plan for a **~1.5–2 mm spacer between the PCB and the 5.5 mm battery**, with the
USB-C dropping into a slot in the case wall.

## Files

| Path | What |
|---|---|
| `led_watch.kicad_pro/.kicad_sch/.kicad_pcb` | KiCad project |
| `libs/led_watch.kicad_sym`, `libs/led_watch.pretty` | Custom symbols/footprints (MFF2 eSIM, U.FL, pogo test pads, battery pads) |
| `docs/BOM_AND_COSTS.md` | Priced BOM + assembly costing |
| `docs/PINOUT.md` | Net/pin map for firmware |
| `docs/led_watch_bom.csv`, `docs/led_watch_schematic.pdf` | BOM export, schematic PDF |
| `fab/gerbers/` | Gerbers + Excellon drill + maps |
| `fab/led_watch_cpl.csv` | KiCad raw pick-and-place |
| `fab/led_watch_cpl_jlc.csv` | CPL in JLCPCB columns/origin |
| `led_watch_2d_view.png` | Top silk render |

## Design decisions carried over from v1

1. **MCP73831 + AO3401A instead of nPM1300** — the nRF9151 and the LEDs both run
   straight from the LiPo (1.7–5.5 V), so the nPM1300's bucks aren't needed, and
   there's no verified QFN-32 symbol for it. You lose the fuel gauge; the R7/R8
   divider on AIN0 replaces it.
2. **U.FL instead of chip antennas** — ceramic chip antennas need a matching
   network tuned on the real board with a VNA. U.FL lets you swap antennas.
3. **SN74LVC1T45 level shifter** on the LED data line, because the nRF9151 GPIO
   domain is 1.8 V and the WS2812B VIH is 0.7 × VDD.

## Verification status

- Schematic: **ERC clean** (0 errors).
- PCB: **fully routed**, 0 unrouted nets, all footprint courtyards clear on
  each side, only the USB-C intentionally overhangs the edge.
- **DRC: 8 errors, 85 warnings.** What's left:
  - 7 × *zone connectivity* between GND pour fragments on F.Cu/B.Cu. The In1.Cu
    layer is a solid GND plane and all GND pads have fanout vias, but the
    surface pours are chopped into islands by the dense routing. Fix in the GUI
    by adding 1–2 stitching vias per flagged island (KiCad highlights them when
    you click the DRC marker), or re-fill with "remove islands" always.
  - 1 × rear solder-mask bridge under J2 (the u.FL footprint's own mask opening
    crosses a via). Cosmetic for a prototype.
  - 1 × hole-to-hole between the USB-C shell hole and a via.
- Warnings are silkscreen reference designators over pads (18+26), isolated
  copper slivers (33), and library-vs-board footprint mismatches (2, the two
  hand-made footprints).

## Read before ordering

- **The MFF2 eSIM footprint is custom and unverified.** Two physical pinout
  variants exist (I/O on pin 2 vs pin 3). The symbol uses
  `1 GND / 2 VPP / 3 I/O / 4-5 NC / 6 CLK / 7 RST / 8 VCC`. Check it against the
  drawing that ships with your 1NCE chip.
- ANT_LTE / ANT_GNSS are short direct traces, **not** 50 Ω net-classed. Add a
  50 Ω class before any certification run.
- Clearance is set to 0.12 mm, min track 0.15 mm, min via 0.5/0.3 — all inside
  JLCPCB's standard capability (0.09 mm), but tighter than a hobby board.
- No mounting holes. The USB-C and the two U.FL connectors are the only
  mechanical anchors.

## Mechanical / enclosure

A complete two-piece enclosure for this board is in
[`../mech/README.md`](../mech/README.md): ⌀40 × 13.5 mm bezel + snap-fit bottom
cover, ⌀35 × 5.5 mm LiPo, 0.5 mm foam pad, light-pipe ring, button plunger,
USB-C cutout, two flex antennas with keep-outs, full interference report and
Blender renders.
