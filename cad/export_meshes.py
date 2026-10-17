# JARVIS CAD -> mesh export for Blender (CAD stays the source of geometry; STL in mm, Z up, front = -Y).
# Run: freecadcmd.exe cad\export_meshes.py   -> build/parts/<PartName>.stl (generated, git-ignored)
import os
import FreeCAD as App
import Mesh
import MeshPart

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.join(root, "build", "parts")
os.makedirs(out, exist_ok=True)
doc = App.openDocument(os.path.join(root, "cad", "JARVIS_blockout.FCStd"))

PARTS = ["Head_Front", "Head_Back", "Body_Shell", "Door_Rear", "Screen", "Camera", "Mic_Top", "Speaker_L",
         "Speaker_R", "Display_Module", "Camera_Module", "Mic_Array", "Pi_5", "Pi_Tray", "Speaker_Driver_L",
         "Speaker_Driver_R", "Reserve_USB_Hub"]
for n in PARTS:
    m = MeshPart.meshFromShape(doc.getObject(n).Shape, LinearDeflection=0.2, AngularDeflection=0.2)
    m.write(os.path.join(out, n + ".stl"))
print(f"exported {len(PARTS)} parts to {out}")
