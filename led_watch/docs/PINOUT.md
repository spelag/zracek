# Pin / net map (for firmware and bring-up)

## nRF9151 (U1) — used pins

| Signal | nRF pin | Net | Connected to |
|---|---|---|---|
| VDD | 14 | VBAT | LiPo / charger output |
| VDD_GPIO | 65 | VIO | tied to VIO (1.8 V) |
| VIO (out) | 29 | VIO | J4 pad 1, U4 VCCA |
| ENABLE | 10 | VBAT | tied high |
| SIM_1V8 (out) | 19 | SIM_VCC | U2 VCC + C18 |
| SIM_CLK | 18 | SIM_CLK | U2 CLK |
| SIM_IO | 17 | SIM_IO | U2 I/O |
| SIM_RST | 16 | SIM_RST | U2 RESET |
| DEC0 | 24 | DEC0 | C17 1 µF |
| ANT (LTE) | 35 | ANT_LTE | J2 u.FL |
| GPS (GNSS) | 42 | ANT_GNSS | J3 u.FL |
| SWDCLK | 3 | SWDCLK | J4 pad 3 |
| SWDIO | 4 | SWDIO | J4 pad 2 |
| RESET | 9 | NRST | J4 pad 4 |
| P0.00 | 56 | LED_DATA_A | U4 A side (level shifter) |
| P0.01 | 57 | BTN_N | SW1 to GND |
| P0.02 | 58 | LED_EN_MCU | R6 → Q2 → Q1 gate |
| AIN0/P0.13 | 73 | BAT_SENSE | R7/R8 divider (VBAT/2), C3 filter |

All other GPIO / TRACE / COEX / MAGPIO / SDATA / SCLK pins are left
unconnected (marked with no-connect flags). All 30+ GND pins are on the GND
plane.

## LED chain

`P0.00 → U4 → L1.DIN → L1.DOUT → L2.DIN → … → L12.DOUT (unconnected)`

Data rate must be the WS2812B protocol (~800 kHz). Drive `LED_EN_MCU` **high**
about 1 ms before the first frame (rails settle, C4 charges) and low after the
last frame plus a short delay.

## Test pads (J4)

Five 1.0 mm SMD pads on the **back**, 1.27 mm pitch:
`1 = VIO (1.8 V), 2 = SWDIO, 3 = SWDCLK, 4 = RESET (active low), 5 = GND`.
No connector — probe with a pogo-pin needle head.

## Battery pads (J5)

Two 2.0 × 1.5 mm pads, 3 mm apart, on the back. Solder the LiPo leads directly;
pad 1 = VBAT, pad 2 = GND.

## Button

`SW1` shorts `BTN_N` to GND. Use the internal pull-up on P0.01. For the
lowest possible sleep current, P0.01 can also be configured as the wake source
(`System OFF` wake on digital).

## Charger

| U3 pin | Function | Net |
|---|---|---|
| 4 | VDD | VBUS (USB-C 5 V) |
| 3 | VBAT | VBAT |
| 5 | PROG | R3 2.2 k to GND (≈450 mA) |
| 1 | STAT | D1 + R4 to VBUS (LED on while charging) |
| 2 | VSS | GND |

## USB-C

Only power is wired: VBUS and GND to the charger, CC1/CC2 with 5.1 k pull-downs
so a USB-C source will supply 5 V. D+/D− and SBU are no-connect — the nRF9151
has no USB device controller in this design, so flashing is over SWD.
