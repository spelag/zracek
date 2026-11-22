"""Copy the KiCad board and point the footprints whose stock 3D models are
missing at the simplified STEP proxies, so kicad-cli can export a complete
mechanical assembly."""
import re
import shutil

SRC = "/mnt/c/Users/juje/temporary/ongoing/adahack/led_watch/led_watch.kicad_pcb"
DST = "/mnt/c/Users/juje/temporary/ongoing/adahack/led_watch/mech/exports/led_watch_assembly.kicad_pcb"
PROX = "C:/Users/juje/temporary/ongoing/adahack/led_watch/mech/reference/proxies"

mapping = {
    "${KICAD10_3DMODEL_DIR}/LED_SMD.3dshapes/LED_WS2812B-2020_PLCC4_2.0x2.0mm.step":
        PROX + "/WS2812B-2020.step",
    "${KICAD10_3DMODEL_DIR}/Connector_USB.3dshapes/USB_C_Receptacle_HRO_TYPE-C-31-M-12.step":
        PROX + "/USB_C_TYPE-C-31-M-12.step",
    "${KICAD10_3DMODEL_DIR}/Package_LGA.3dshapes/Nordic_nRF9151-LAxx_LGA-80-33EP_12.1x11.1mm_P0.5mm.step":
        PROX + "/nRF9151-LACA.step",
}

s = open(SRC, encoding="utf-8").read()
for old, new in mapping.items():
    n = s.count(old)
    s = s.replace(old, new)
    print("patched %d x %s" % (n, old.split("/")[-1]))

# Add the missing eSIM model to U2.
parts = s.split("\n\t(footprint ")
changed = False
for i, p in enumerate(parts):
    if '(property "Reference" "U2"' in p and "(model " not in p:
        model = (
            '\n\t\t(model "%s/MFF2_eSIM.step"\n'
            "\t\t\t(offset (xyz 0 0 0))\n"
            "\t\t\t(scale (xyz 1 1 1))\n"
            "\t\t\t(rotate (xyz 0 0 0))\n"
            "\t\t)"
        ) % PROX
        # insert just before the group's closing marker
        idx = p.rstrip().rfind("\n\t)")
        if idx == -1:
            raise SystemExit("could not find U2 footprint end")
        parts[i] = p[:idx] + model + p[idx:]
        changed = True
        print("added eSIM model")
        break
if not changed:
    print("WARNING: U2 model not added")
s = "\n\t(footprint ".join(parts)
open(DST, "w", encoding="utf-8").write(s)
print("wrote", DST)
