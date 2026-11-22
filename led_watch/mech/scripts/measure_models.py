import os
import FreeCAD
import Part

root = r"C:\Program Files\KiCad\10.0\share\kicad\3dmodels"
files = [
    r"Button_Switch_SMD.3dshapes\SW_SPST_EVQP0.step",
    r"Connector_Coaxial.3dshapes\U.FL_Hirose_U.FL-R-SMT-1_Vertical.step",
    r"Package_TO_SOT_SMD.3dshapes\SOT-23.step",
    r"Package_TO_SOT_SMD.3dshapes\SOT-23-5.step",
    r"Package_TO_SOT_SMD.3dshapes\SOT-23-6.step",
    r"Capacitor_SMD.3dshapes\C_0402_1005Metric.step",
    r"Capacitor_SMD.3dshapes\C_0603_1608Metric.step",
    r"Resistor_SMD.3dshapes\R_0402_1005Metric.step",
    r"LED_SMD.3dshapes\LED_0603_1608Metric.step",
]
for f in files:
    p = os.path.join(root, f)
    try:
        s = Part.Shape()
        s.read(p)
        bb = s.BoundBox
        print("%-55s X %7.3f..%7.3f  Y %7.3f..%7.3f  Z %7.3f..%7.3f" % (
            os.path.basename(f), bb.XMin, bb.XMax, bb.YMin, bb.YMax, bb.ZMin, bb.ZMax))
    except Exception as e:
        print("ERR", f, e)
