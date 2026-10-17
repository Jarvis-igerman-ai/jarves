# JARVES Pebble A head fit study (S1.T03R1, Issue #3). Analytic convex-geometry check - NOT CAD, NOT a fit guarantee.
# Head = ellipsoid shell, front cut by a flat circular face facet, top cut by a flat mic "crown" cap.
# Packs: display stack (+ rear connector keep-out), Camera Module 3 Wide above the display, mic board under the cap.
# Run: freecadcmd design/review/pebble_fit_study.py   (any Python 3 works)
#
# MEASURED from manufacturer STEP files on 2026-10-17 (FreeCAD bbox):
#   Waveshare 5" 1080x1080 HDMI round (SKU 24369): outline Ø150, panel 11.5, rear driver board 85.5x58, total depth 18.05
#   Waveshare 4" 720x720 DSI round (SKU 24603):    outline Ø126, panel 10.0, rear board 85.5x65, total depth 18.2
#   Seeed ReSpeaker XVF3800 (with-XIAO STEP):      PCB ~100.5 x 101.8, total 9.03
# VERIFIED (raspberrypi.com docs): Camera Module 3 Wide ~25 x 24 x 12.4.
# PROVISIONAL: wall 3, gasket 3, lips/margins, connector keep-outs, ReSpeaker Lite height, board positions.
import math

WALL, GASKET, LIP = 3.0, 3.0, 4.0
CAM = (25.0, 12.4, 24.0)                       # x, depth, height
DISPLAYS = {  # radius, depth, rear board x-range, z-range (rel. centre), connector keep-out depth behind board
    '5in HDMI (D150)': (75.0, 18.05, (-44.5, 41.0), (-30.0, 28.0), 25.0),   # HDMI plug + bend
    '4in DSI (D126)': (63.0, 18.2, (-42.8, 42.7), (-37.0, 28.0), 10.0),     # FPC ribbon fold
}
MICS = {"XVF3800 4-mic": (100.5, 101.8, 9.03), "ReSpeaker Lite 2-mic": (86.0, 35.0, 10.0),
        "any array in raised crown pod": (1.0, 1.0, 1.0)}   # board outside the head: only display+camera checked
HEADS = {"compact W205 D~130": (102.5, 85.0, 92.5), "mid W205 D~150": (102.5, 100.0, 92.5),
         "growth W215 D~160": (107.5, 100.0, 97.5)}


def inside(p, r):
    return sum((p[i] / r[i]) ** 2 for i in range(3)) <= 1.0


def overlap(a, b):
    return all(a[i][0] < b[i][1] and b[i][0] < a[i][1] for i in range(3))


def corners(b):
    return [(x, y, z) for x in b[0] for y in b[1] for z in b[2]]


def pack(head, disp, mic, cap_r):
    rx, ry, rz = head
    R, dd, bx, bz, keep = disp
    inner = (rx - WALL, ry - WALL, rz - WALL)
    zc = rz * math.sqrt(max(0.0, 1 - (cap_r / rx) ** 2))         # outer cap plane
    zin = zc - WALL
    for dz10 in range(0, -401, -10):                                # lower the display in 1 mm steps
        dz = dz10 / 10
        cam_top = dz + R + 1 + CAM[2]
        face_r = max(abs(dz) + R + LIP, abs(dz + R + 1 + CAM[2] / 2) + 8)   # facet must also show the camera lens
        if face_r >= min(rx, rz):
            continue
        f = ry * math.sqrt(1 - (face_r / min(rx, rz)) ** 2)        # facet plane distance in front of centre
        y0 = -f + WALL
        if cam_top > zin:
            continue
        ring = [(R * math.cos(a), y, dz + R * math.sin(a)) for a in [i * math.pi / 18 for i in range(36)]
                for y in (y0, y0 + dd)]
        if not all(inside(p, inner) and p[2] <= zin for p in ring):
            continue
        cam = ((-CAM[0] / 2, CAM[0] / 2), (y0, y0 + CAM[1]), (cam_top - CAM[2], cam_top))
        if not all(inside(c, inner) for c in corners(cam)):
            continue
        panel = ((-R, R), (y0, y0 + dd), (dz - R, dz + R))
        rear = (bx, (y0 + 7.0, y0 + dd + keep), (dz + bz[0], dz + bz[1]))
        mw, ml, mh = mic
        for drop in range(0, 60):
            top = zin - GASKET - drop
            for yc in range(-60, 61):
                box = ((-mw / 2, mw / 2), (yc - ml / 2, yc + ml / 2), (top - mh, top))
                if box[1][0] < y0 or not all(inside(c, inner) for c in corners(box)):
                    continue
                if overlap(box, panel) or overlap(box, rear) or overlap(box, cam):
                    continue
                return dict(dz=dz, face_r=face_r, depth=f + ry, height=zc + rz, port=WALL + GASKET + drop)
    return None


print("head | display | mic | best crown cap -> display offset, facet dia, head D x H, mic port length")
for hn, head in HEADS.items():
    for dn, disp in DISPLAYS.items():
        for mn, mic in MICS.items():
            best = None
            for cap_r in (50, 55, 60, 65, 70, 76):
                r = pack(head, disp, mic, cap_r)
                if r and (best is None or r["port"] < best[1]["port"] - 0.5):
                    best = (cap_r, r)
            if best is None:
                print(f"RES {hn} | {dn} | {mn} | NO FIT")
            else:
                c, r = best
                print(f"RES {hn} | {dn} | {mn} | crown D{2 * c} -> display {r['dz']:+.0f} mm, facet D{2 * r['face_r']:.0f}, "
                      f"head D{r['depth']:.0f} x H{r['height']:.0f}, port ~{r['port']:.0f} mm")
