# JARVIS CAD smoke test (fast, headless, one process). Exits non-zero on any failure.
# Run: freecadcmd cad/smoke_test.py   (Windows: "C:\...\FreeCAD 1.1\bin\freecadcmd.exe" cad\smoke_test.py)
# Covers blockout (S1.T01) and Pebble A (S1.T03R2): generation, validity, geometric checks, one parameter
# regression each on a temp copy.
# Never touches the committed .FCStd files.
import os
import sys
import shutil
import tempfile
import FreeCAD as App

here = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp(prefix="jarvis_smoke_")
errors = []


def run(name, fn):
    try:
        fn()
        print(f"ok   {name}")
    except Exception as e:  # noqa: BLE001 - report every failure, keep going
        errors.append(f"{name}: {e}")
        print(f"FAIL {name}: {e}")


model = os.path.join(tmp, "smoke.FCStd")
os.environ["JARVIS_CAD_OUT"] = model


def generate():  # build script raises on invalid/recompute errors
    exec(compile(open(os.path.join(here, "build_blockout.py")).read(), "build_blockout.py", "exec"),
         {"__name__": "build_blockout"})
    assert os.path.getsize(model) > 1000, "FCStd not written"


lib = {"__name__": "check_layout_lib"}
exec(compile(open(os.path.join(here, "check_layout.py")).read(), "check_layout.py", "exec"), lib)
run("generate + recompute valid", generate)
doc = [None]


def geometry():
    doc[0] = App.openDocument(model)
    fails, report = lib["check"](doc[0])
    print("\n".join("     " + r for r in report))
    assert not fails, "; ".join(fails)


def regression():  # Width 200 -> 220 must widen body, move speakers, and keep the layout valid
    copy = os.path.join(tmp, "copy.FCStd")
    shutil.copy(model, copy)
    d = App.openDocument(copy)
    w0 = d.Body_Shell.Shape.BoundBox.XLength
    x0 = d.Speaker_Driver_R.Shape.BoundBox.Center.x
    d.Params.set("B2", "=220 mm")
    d.recompute()
    w1, x1 = d.Body_Shell.Shape.BoundBox.XLength, d.Speaker_Driver_R.Shape.BoundBox.Center.x
    assert abs(w1 - w0 - 20) < 1e-3, f"body width {w0}->{w1}"
    assert abs(x1 - x0 - 5) < 1e-3, f"speaker x {x0}->{x1}"
    fails, _ = lib["check"](d)
    assert not fails, "; ".join(fails)


run("geometric checks", geometry)
run("parameter-change regression (Width 220)", regression)

# ---- Pebble A (S1.T03R2) ----
pebble = os.path.join(tmp, "pebble.FCStd")
os.environ["JARVIS_PEBBLE_OUT"] = pebble
plib = {"__name__": "check_pebble_lib"}
exec(compile(open(os.path.join(here, "check_pebble.py")).read(), "check_pebble.py", "exec"), plib)


def pebble_generate():
    exec(compile(open(os.path.join(here, "build_pebble.py")).read(), "build_pebble.py", "exec"),
         {"__name__": "build_pebble"})
    assert os.path.getsize(pebble) > 1000, "pebble FCStd not written"


def pebble_geometry():
    fails, report, _ = plib["check"](App.openDocument(pebble))
    print("\n".join("     " + r for r in report if not r.startswith("print part")))
    assert not fails, "; ".join(fails)


def pebble_regression():  # HeadRX 107.5 -> 112 must widen the head shells and keep every check passing
    copy = os.path.join(tmp, "pebble_copy.FCStd")
    shutil.copy(pebble, copy)
    d = App.openDocument(copy)
    w0 = d.Head_Back.Shape.optimalBoundingBox().XLength
    row = next(i for i in range(1, 40) if d.Params.get(f"A{i}") == "HeadRX")
    d.Params.set(f"B{row}", "=112 mm")
    d.recompute()
    w1 = d.Head_Back.Shape.optimalBoundingBox().XLength
    assert abs(w1 - w0 - 9) < 0.5, f"head width {w0:.1f}->{w1:.1f}"
    fails, _, _ = plib["check"](d)
    assert not fails, "; ".join(fails)


run("pebble generate + recompute valid", pebble_generate)
run("pebble checks (containment, collisions, load path, print size)", pebble_geometry)
run("pebble parameter regression (HeadRX 112)", pebble_regression)
shutil.rmtree(tmp, ignore_errors=True)
print("SMOKE RESULT:", "FAIL " + "; ".join(errors) if errors else "PASS")
sys.stdout.flush()
os._exit(1 if errors else 0)
