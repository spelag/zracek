# Bill of Materials and cost estimate (v2, ⌀34 mm board)

Prices are **estimates** for single-unit / small-quantity purchasing at LCSC,
Mouser or Arrow (EUR, excl. VAT and shipping), gathered around Nov 2026.
Check them before you order — distributor stock and pricing move.

## 1. Board BOM (everything soldered onto the PCB)

| # | Ref | Part | Package | Qty | Unit € | Total € | Notes |
|---|---|---|---|---|---|---|---|
| 1 | U1 | **Nordic nRF9151-LACA** | LGA-80 12.1×11.1 | 1 | 23.00 | 23.00 | Cortex-M33 + LTE-M/NB-IoT + GNSS |
| 2 | U2 | **1NCE MFF2 eSIM** | QFN-8 5×6 | 1 | 2.50 | 2.50 | chip only; plan separate |
| 3 | U3 | MCP73831T-2ACI/OT | SOT-23-5 | 1 | 0.55 | 0.55 | LiPo linear charger, Rprog 2.2 k → ~450 mA |
| 4 | U4 | SN74LVC1T45DBVR | SOT-23-6 | 1 | 0.42 | 0.42 | 1.8 V → VBAT logic level shift for LED data |
| 5 | Q1 | AO3401A | SOT-23 | 1 | 0.06 | 0.06 | P-FET LED rail switch |
| 6 | Q2 | MMBT3904 | SOT-23 | 1 | 0.03 | 0.03 | gate driver for Q1 |
| 7 | L1–L12 | WS2812B-2020 | PLCC-4 2.0×2.0 | 12 | 0.06 | 0.72 | addressable RGB ring |
| 8 | D1 | LED green 0603 | 0603 | 1 | 0.03 | 0.03 | charge indicator |
| 9 | J1 | USB-C receptacle 16P, HRO TYPE-C-31-M-12 | SMD | 1 | 0.25 | 0.25 | charging |
| 10 | J2, J3 | U.FL / IPEX MHF1 (Hirose U.FL-R-SMT-1) | SMD | 2 | 0.40 | 0.80 | LTE + GNSS antenna |
| 11 | J4 | 5 × SMD test pads (pogo) | — | 1 | 0.00 | 0.00 | SWD: VIO, SWDIO, SWDCLK, RESET, GND; no part, bare pads |
| 12 | J5 | 2 × solder pads, 3 mm pitch | — | 1 | 0.00 | 0.00 | battery wires soldered directly; no part |
| 13 | SW1 | Panasonic EVQP0N02B tactile | 6×6 mm SMD | 1 | 0.30 | 0.30 | centre button |
| 14 | R1–R8 | 0402 resistors (5.1k×2, 2.2k, 1k, 100k, 10k, 1M×2) | 0402 | 8 | 0.004 | 0.03 | CC, PROG, gate, base, divider |
| 15 | C1, C2, C4 | 4.7 µF / 4.7 µF / 10 µF X5R | 0603 | 3 | 0.02 | 0.06 | VBUS, VBAT, VLED bulk |
| 16 | C3, C5–C18 | 100 nF ×15, 1 µF ×1 | 0402 | 16 | 0.005 | 0.08 | per-LED decoupling + filters |
| | | **Parts subtotal** | | **51** | | **€28.83** | |

Not counted above, buys separately:

| Item | € | Notes |
|---|---|---|
| 1NCE Lifetime Flat plan | 12.00 | 500 MB + 250 SMS / 10 years, one per eSIM |
| 450 mAh LiPo (402530 / 502030, with PCM) | 4.50 | 3.0–4.0 mm thick; check your case |
| LTE-M antenna, u.FL, ~100 mm flex | 1.50 | e.g. Taoglas FXP73 or any MHF1 LTE antenna |
| GNSS antenna, u.FL, active | 2.00 | e.g. Taoglas FXP07 or Molex 146153 |
| Case, lens, button cap | — | mechanical, depends on your design |

**Per-board electronics: ≈ €28.85 + €12.00 (plan) + €4.50 (battery) + €3.50 (antennas) ≈ €49.20.**

That lands almost exactly on your original estimate — the PMIC simplification
paid for the two u.FL antennas.

### Where you can save

- U1 is 78 % of the parts cost. Buying 10+ brings it to ~€20–21.
- 1NCE: the €12 plan is per chip; there is no multi-chip discount.
- Passives are negligible; don't optimise them.

## 2. PCB fabrication + assembly (JLCPCB, 5 boards)

Board: 4-layer, 1.6 mm, ⌀34 mm circle, ENIG, impedance not controlled,
double-sided SMT (LEDs and the SiP on top, eSIM/caps/connectors on the bottom).

| Line | Estimate (5 pcs) | Notes |
|---|---|---|
| 4-layer PCB, ENIG, ⌀34 mm circle | €12 | 5 pcs |
| Assembly setup + stencil (Standard) | €31 | 0.5 mm-pitch LGA forces Standard tier |
| Extended-part feeder fees | €14 | ~9 unique non-basic reels (nRF9151, USB-C, tact switch, WS2812B, MCP73831, SN74LVC1T45, AO3401, MMBT3904, U.FL ×2) |
| Placement / joint charges | €6 | ~51 placements |
| DHL + EU VAT (IOSS, 22 %) | €28 | |
| **Total, 5 assembled boards** | **≈ €90** | **≈ €18 / board** |

**Finished cost per unit, 5-unit batch: ≈ €29 parts + €18 assembly + €12 plan + €4.50 battery + €3.50 antennas ≈ €68.**

## 3. What to have JLCPCB assemble, and what you do at home

1. Order everything **except** U2 (eSIM) and J2/J3 (u.FL, optional — they're
   easy to hand-solder) as a turnkey JLCPCB order using `fab/led_watch_cpl.csv`
   and the BOM above.
2. Reflow the MFF2 eSIM with hot air / hot plate at home (it's a 5×6 mm QFN-8;
   paste + hot air is fine). **Check the pinout against 1NCE's drawing first.**
3. Solder the battery wires to the two pads at the board edge (J5).
4. Hold a 5-pin pogo probe on the J4 test pads (VIO, SWDIO, SWDCLK, RESET, GND) and flash.
5. Snap on the two u.FL antennas.

## 4. What I could not price

- Watch case / button cap / lens / gasket — mechanical, your call.
- Antenna tuning if you later move to on-board chip antennas (needs a VNA).
- Certification (CE/RED) if this ever ships commercially — LTE + GNSS + a
  radio device means RED testing, which dwarfs the hardware cost.
