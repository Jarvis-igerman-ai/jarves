"""JARVES camera (SW-V2 WP-J; plan docs/plans/CAMERA_PLAN.md, setup docs/CAMERA_SETUP.md).

    backends.py  camera adapters (fake / OpenCV webcam / picamera2) + availability probe that never opens the sensor
    frames.py    RAM-only frames, closed-shutter check, in-memory image encoding for one cloud request
    detect.py    local face detection (YuNet, Haar fallback) and opt-in embeddings (SFace)
    presence.py  presence + gaze logic (pure)
    faces.py     opt-in face store (embeddings only, per-person delete)
    switches.py  dashboard switches camera / face_recognition / camera_when_away (all default OFF)
    vision.py    one still -> backend vision model through the existing Responses path
    service.py   CameraService: the single owner of the sensor and of the privacy rules
    wire.py      Core integration (attach) + untrusted-image guard

Messages to the face: `face.set_gaze {x, y, confidence}` (-1..1, 0 confidence = stop tracking) and
`camera.indicator {on, flash}`. Importing this package never imports OpenCV or picamera2.
"""
