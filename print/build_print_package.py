# JARVES print-shop package (S1.T03R4). Exports ONLY printed parts as print-oriented STL, checks bed fit with
# margin on real bed classes, slices every part with PrusaSlicer (with and without supports) and writes
# print/QUOTE_PACKAGE.md + print/print_report.json. Estimates only - not a shop quote.
# Run: freecadcmd print/build_print_package.py   (PrusaSlicer path via env PRUSA_SLICER or default below)
import json
import math
import os
import re
import subprocess
import FreeCAD as App
import MeshPart
from FreeCAD import Vector, Rotation

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pdir = os.path.join(root, "print")
SLICER = os.environ.get("PRUSA_SLICER") or os.path.join(
    os.environ["LOCALAPPDATA"], "Programs", "PrusaSlicer-2.9.6", "PrusaSlicer-2.9.6", "prusa-slicer-console.exe")
tmp = os.path.join(os.environ.get("TEMP", "/tmp"), "jarves_slice")
os.makedirs(tmp, exist_ok=True)
for sub in ("stl", "stl/options", "stl/split"):
    os.makedirs(os.path.join(pdir, sub), exist_ok=True)

doc = App.openDocument(os.path.join(root, "cad", "JARVIS_pebble.FCStd"))
sh = doc.getObject("Params")
parts, i = {}, 2
while True:
    try:
        n = sh.get(f"E{i}")
    except Exception:
        break
    parts[n] = dict(cat=sh.get(f"F{i}"), kind=sh.get(f"G{i}"), step=int(float(sh.get(f"H{i}"))),
                    note=str(sh.get(f"J{i}")), pid=sh.get(f"K{i}"), rot=str(sh.get(f"L{i}")),
                    group=sh.get(f"M{i}"))
    i += 1
fin = json.load(open(os.path.join(root, "blender", "finishes.json")))
COLOUR_C = {  # finish C per print (colour change = one filament swap at a layer height, single extruder)
    "Head_Front": "white + dark face ring (filament change after the face layers, printed face-down)",
    "Head_Back": "white", "Neck_Column": "sky blue", "Base_Door": "sky blue", "LED_Blank_Front": "sky blue",
    "LED_Blank_Back": "sky blue", "Base_Shell": "sky blue + navy band (2 filament changes, printed top-down)",
    "LED_Diffuser_Front": "natural/translucent PETG", "LED_Diffuser_Back": "natural/translucent PETG"}
OPTIONAL = {"LED_Diffuser_Front", "LED_Diffuser_Back"}   # only when the optional LED ring is fitted

# bed classes (usable X x Y x Z mm) and fit margin: 5 mm brim each side + 3 mm safety each side
BEDS = {"250x210 (Prusa MK3/MK4 class; 3D Lab Haifa stated max 250x210x220)": (250, 210, 220),
        "220x220 (Ender-3 class)": (220, 220, 250), "256x256 (Bambu P1S/X1C/A1 class)": (256, 256, 256)}
MARGIN = 16.0
SETTINGS = dict(layer=0.2, perimeters=3, top=5, bottom=4, infill="15% gyroid", material="PETG 1.75 mm, 1.27 g/cm3",
                support="organic (tree), auto, threshold 50 deg", brim="5 mm on large parts",
                speeds="i3-class reference (perimeters 45, ext 25, infill 80, travel 150 mm/s, accel 1250)")


def oriented(shape, rot):
    s = shape.copy()
    axis, ang = rot.split(",")
    ang = float(ang)
    if ang:
        v = {"X": Vector(1, 0, 0), "Y": Vector(0, 1, 0), "Z": Vector(0, 0, 1)}[axis]
        s.rotate(Vector(0, 0, 0), v, ang)
    b = s.BoundBox
    s.translate(Vector(-b.Center.x, -b.Center.y, -b.ZMin))
    return s


def write_stl(shape, path):
    MeshPart.meshFromShape(shape, LinearDeflection=0.08, AngularDeflection=0.25).write(path)


def bed_fit(dx, dy, dz):
    res = {}
    for name, (bx, by, bz) in BEDS.items():
        ok = dz <= bz and any(a + MARGIN <= bx and b + MARGIN <= by for a, b in ((dx, dy), (dy, dx)))
        res[name] = ok
    return res


def slice_stl(stl, supports, big):
    out = os.path.join(tmp, os.path.basename(stl) + (".sup" if supports else ".nosup") + ".gcode")
    cmd = [SLICER, "--export-gcode", "--output", out, "--gcode-flavor", "marlin2",
           "--bed-shape", "0x0,400x0,400x400,0x400", "--max-print-height", "400", "--center", "200,200",
           "--nozzle-diameter", "0.4", "--filament-diameter", "1.75", "--filament-density", "1.27",
           "--temperature", "240", "--bed-temperature", "80", "--layer-height", "0.2",
           "--first-layer-height", "0.2", "--perimeters", "3", "--top-solid-layers", "5",
           "--bottom-solid-layers", "4", "--fill-density", "15%", "--fill-pattern", "gyroid",
           "--perimeter-speed", "45", "--external-perimeter-speed", "25", "--infill-speed", "80",
           "--solid-infill-speed", "40", "--top-solid-infill-speed", "30", "--travel-speed", "150",
           "--first-layer-speed", "20", "--support-material-speed", "50", "--bridge-speed", "30",
           "--machine-limits-usage", "time_estimate_only", "--machine-max-acceleration-extruding", "1250",
           "--machine-max-acceleration-travel", "1250", "--machine-max-feedrate-x", "200",
           "--machine-max-feedrate-y", "200", "--skirts", "0", "--brim-width", "5" if big else "0"]
    if supports:
        cmd += ["--support-material", "--support-material-auto", "--support-material-style", "organic",
                "--support-material-threshold", "50"]
    cmd.append(stl)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not os.path.exists(out):
        return {"error": (r.stdout + r.stderr)[-400:]}
    txt = open(out, errors="ignore").read()[-30000:]
    g = re.search(r"; (?:total )?filament used \[g\] = ([\d.]+)", txt)
    tm = re.search(r"; estimated printing time \(normal mode\) = ([^\n]+)", txt)
    os.remove(out)
    secs = 0
    if tm:
        for val, unit in re.findall(r"(\d+)([dhms])", tm.group(1)):
            secs += int(val) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return {"grams": float(g.group(1)) if g else None, "seconds": secs}


def tier_price(grams):
    """3D Lab Haifa published tiers (2026-10-17): from 30 ILS <=50 g; 100-300 ILS for 50-250 g; from 300 ILS larger."""
    if grams <= 50:
        return (30, 60)
    if grams <= 250:
        lo = 100 + (grams - 50) / 200 * 200
        return (round(lo * 0.85), round(min(300, lo * 1.15)))
    return (round(300 * grams / 250 * 0.85), round(300 * grams / 250 * 1.25))


jobs = []
import Part
import shutil
shutil.rmtree(os.path.join(pdir, "stl"), ignore_errors=True)       # no stale STLs from older designs
for sub in ("stl", "stl/options", "stl/split"):
    os.makedirs(os.path.join(pdir, sub), exist_ok=True)
leaders = sorted((n for n, p in parts.items() if (p["kind"] == "printed" or n in OPTIONAL) and p["group"] == n),
                 key=lambda n: parts[n]["pid"])
for n in leaders:                                   # one STL per physical print: colour zones are fused back in
    p = parts[n]
    members = [m for m, q in parts.items() if q["group"] == n]
    shp = doc.getObject(members[0]).Shape
    for m in members[1:]:
        shp = shp.fuse(doc.getObject(m).Shape)
    s = oriented(shp.removeSplitter() if len(members) > 1 else shp, p["rot"])
    sub = "options" if n in OPTIONAL else ""
    path = os.path.join(pdir, "stl", sub, f"{p['pid']}_{n}.stl")
    write_stl(s, path)
    jobs.append(dict(pid=p["pid"], name=n, file=os.path.relpath(path, root).replace("\\", "/"), shape=s,
                     colour=COLOUR_C.get(n, "any colour (internal)"), note=p["note"], optional=n in OPTIONAL,
                     split=False))
# split fallback for printers whose bed cannot take the Ø210 base parts (front/back halves at y = 0)
for n in ("Base_Shell", "Base_Floor"):
    p = parts[n]
    full = doc.getObject(n).Shape
    for m, q in parts.items():
        if q["group"] == n and m != n:
            full = full.fuse(doc.getObject(m).Shape)
    for half, y0 in (("front", -300), ("back", 0)):
        cutter = App.ActiveDocument.addObject("Part::Box", "_tmpcut")
        cutter.Length, cutter.Width, cutter.Height = 600, 300, 600
        cutter.Placement.Base = Vector(-300, y0, -100)
        doc.recompute()
        s = oriented(full.common(cutter.Shape), p["rot"])
        doc.removeObject(cutter.Name)
        path = os.path.join(pdir, "stl", "split", f"{p['pid']}{half[0]}_{n}_{half}.stl")
        write_stl(s, path)
        jobs.append(dict(pid=f"{p['pid']}{half[0]}", name=f"{n} ({half} half)", file=os.path.relpath(path, root).replace("\\", "/"),
                         shape=s, colour="sky blue" if n == "Base_Shell" else "any (internal)",
                         note="SPLIT fallback - needs 2 splice plates + 4 M3 screws per seam (not modelled)",
                         optional=False, split=True))

report = []
for j in jobs:
    b = j["shape"].BoundBox
    dims = (round(b.XLength, 1), round(b.YLength, 1), round(b.ZLength, 1))
    big = max(dims[:2]) > 120
    stl = os.path.join(root, j["file"])
    sup = slice_stl(stl, True, big)
    nos = slice_stl(stl, False, big)
    g = sup.get("grams")
    rec = dict(pid=j["pid"], name=j["name"], file=j["file"], dims_mm=dims, fit=bed_fit(*dims),
               grams=g, support_grams=round(g - nos["grams"], 1) if g and nos.get("grams") else None,
               hours=round(sup.get("seconds", 0) / 3600, 2), colour=j["colour"], note=j["note"],
               optional=j["optional"], split=j["split"], error=sup.get("error") or nos.get("error"))
    rec["price_ils"] = tier_price(g * 1.10) if g else None   # +10 % failure/reprint margin
    report.append(rec)
    print("SLICED", rec["pid"], rec["name"], dims, rec["grams"], "g", rec["hours"], "h", "sup", rec["support_grams"],
          "ERR" if rec["error"] else "")
json.dump(dict(settings=SETTINGS, beds=list(BEDS), margin_mm=MARGIN, parts=report),
          open(os.path.join(pdir, "print_report.json"), "w"), indent=1)

# ---------------- quote-ready package (markdown) ----------------
main = [r for r in report if not r["optional"] and not r["split"]]
L = ["# JARVES Pebble A — print-shop package (quote-ready draft)", "",
     "_Generated by `print/build_print_package.py` (PrusaSlicer 2.9.6 CLI). **Estimates only — not a shop quote.** "
     "Printed parts only; purchased parts, electronics, reserved volumes are excluded._", "",
     "**Material:** PETG (heat near the Pi; PLA not recommended for the base/neck). **Colours (finish C):** "
     "head white, base/door/mic cap/collar/top flange sky blue, LED diffuser natural/translucent, internal parts any colour. "
     "Fabric band, acrylic face cover and aluminium tube are bought separately.", "",
     f"**Slicer settings:** {SETTINGS['layer']} mm layers, {SETTINGS['perimeters']} perimeters, {SETTINGS['infill']} infill, "
     f"supports {SETTINGS['support']}, brim {SETTINGS['brim']}, speeds {SETTINGS['speeds']}. "
     f"Bed fit uses {MARGIN:.0f} mm extra (brim + safety).", "",
     "| ID | Part | File | Qty | Size on bed (mm) | Fits 250x210 | Fits 220x220 | Fits 256x256 | PETG g | of which supports g | Time h | Colour | Estimate ILS* |",
     "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
yn = lambda b: "yes" if b else "**NO**"
tot_g = tot_h = tot_lo = tot_hi = 0
for r in main + [r for r in report if r["split"]] + [r for r in report if r["optional"]]:
    f = list(r["fit"].values())
    pr = r["price_ils"] or (0, 0)
    tag = " (split option)" if r["split"] else " (optional)" if r["optional"] else ""
    L.append(f"| {r['pid']} | {r['name']}{tag} | [{os.path.basename(r['file'])}](../{r['file']}) | 1 | "
             f"{r['dims_mm'][0]} x {r['dims_mm'][1]} x {r['dims_mm'][2]} | {yn(f[0])} | {yn(f[1])} | {yn(f[2])} | "
             f"{r['grams']} | {r['support_grams']} | {r['hours']} | {r['colour']} | {pr[0]}-{pr[1]} |")
    if r in main:
        tot_g += r["grams"] or 0
        tot_h += r["hours"]
        tot_lo += pr[0]
        tot_hi += pr[1]
L += ["", f"**Totals (one-piece set, without split/optional parts):** {tot_g:.0f} g PETG, {tot_h:.1f} h printing "
      f"(i3-class reference speed; CoreXY shop printers are typically ~2x faster), per-part tier estimate "
      f"**{tot_lo}-{tot_hi} ILS**.", "",
      "*Estimate method: 3D Lab Haifa published weight tiers (https://3dlab.co.il/print-service.html, 2026-10-17: "
      "'from 30 ILS' up to 50 g, 100-300 ILS for 50-250 g, 'from 300 ILS' larger), applied per part to slicer grams "
      "+10 % failure margin. VAT not stated on the page; support removal / post-processing / delivery not included. "
      "Shops often discount multi-part jobs — a real quote can be lower or higher.", "",
      f"**Cross-check by weight:** the same tiers imply about 1.2-2.0 ILS per gram (300 ILS / 250 g ... 100 ILS / 50 g). "
      f"Applied to the whole set ({tot_g * 1.1:.0f} g incl. 10 % margin): **{tot_g * 1.1 * 1.2:.0f}-{tot_g * 1.1 * 2.0:.0f} ILS**. "
      "Large parts are usually priced below the small-part rate, so the lower half of the range is more plausible."]
open(os.path.join(pdir, "QUOTE_PACKAGE.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
print("PACKAGE DONE", len(report), "jobs", round(tot_g), "g", round(tot_h, 1), "h", tot_lo, tot_hi)
