"""Create simplified STEP proxies for parts missing from the KiCad 3D library.
Models are centred on XY with Z=0 at the board surface, matching KiCad's
placement convention (back-side models are mirrored by KiCad)."""
import os
import FreeCAD as App
import Part

OUT = r"C:\Users\juje\temporary\ongoing\adahack\led_watch\mech\reference\proxies"
os.makedirs(OUT, exist_ok=True)


def rounded_box(name, sx, sy, sz, fillet=0.08):
    b = Part.makeBox(sx, sy, sz)
    b.translate(App.Vector(-sx / 2.0, -sy / 2.0, 0.0))
    try:
        b = b.makeFillet(fillet, b.Edges)
    except Exception as exc:
        print("fillet skipped for", name, exc)
    b.exportStep(os.path.join(OUT, name + ".step"))
    bb = b.BoundBox
    print("%s  %.3f x %.3f x %.3f  bbox z %.3f..%.3f" %
          (name, bb.XLength, bb.YLength, bb.ZLength, bb.ZMin, bb.ZMax))


# WS2812B-2020: 2.0 x 2.0 x 0.65 mm; small raised lens
led_body = Part.makeBox(2.0, 2.0, 0.55)
led_body.translate(App.Vector(-1.0, -1.0, 0.0))
lens = Part.makeCylinder(0.55, 0.10, App.Vector(0, 0, 0.55))
led = led_body.fuse(lens).removeSplitter()
led.exportStep(os.path.join(OUT, "WS2812B-2020.step"))
print("WS2812B-2020 bbox", led.BoundBox)

# USB-C HRO TYPE-C-31-M-12: 8.94 x 7.35 x 3.1 mm, centred on the footprint origin
usb = Part.makeBox(8.94, 7.30, 3.10)
usb.translate(App.Vector(-4.47, -3.65, 0.0))
# round the mouth end very slightly so it reads as a connector in renders
usb.exportStep(os.path.join(OUT, "USB_C_TYPE-C-31-M-12.step"))
print("USB-C bbox", usb.BoundBox)

# nRF9151-LACA: 12.1 x 11.1 x 0.90 mm
nrf = Part.makeBox(12.1, 11.1, 0.90)
nrf.translate(App.Vector(-6.05, -5.55, 0.0))
try:
    nrf = nrf.makeFillet(0.25, nrf.Edges)
except Exception as exc:
    print("nrf fillet skipped", exc)
nrf.exportStep(os.path.join(OUT, "nRF9151-LACA.step"))
print("nRF9151 bbox", nrf.BoundBox)

# 1NCE MFF2 eSIM: 5 x 6 x 0.60 mm
esim = Part.makeBox(5.0, 6.0, 0.60)
esim.translate(App.Vector(-2.5, -3.0, 0.0))
esim.exportStep(os.path.join(OUT, "MFF2_eSIM.step"))
print("MFF2 bbox", esim.BoundBox)

print("done ->", OUT)
