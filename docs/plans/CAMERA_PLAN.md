# Plan: JARVES camera (Issue #17, camera item; physical side tracked in #8)

> **Research and plan only.** No hardware was bought, connected or switched on, and no camera code was written.
> Nothing here is authorized until `docs/NOW.md` says so. Sources checked **2026-10-19** (links at the end).
> Prices are vendor list prices; **Israeli VAT-inclusive ILS stock is UNVERIFIED** and must be checked before any purchase
> (PROJECT_PRINCIPLES: local stock, warranty, substitutes).

## 1. Recommendation (short)

- **Hardware:** keep the part already in the CAD baseline: **Raspberry Pi Camera Module 3 Wide** (E03, ~25 x 24 x 12.4 mm,
  120 deg, autofocus) on the **500 mm Pi 5 ribbon** (22->15 pin), mounted behind the face window with 2 x M2 self-tapping
  screws. Add a **printed sliding privacy shutter** to the camera window in the next CAD round.
- **Software:** `picamera2` (libcamera) on the Pi; on Windows a USB-webcam adapter for development. All camera access sits
  behind the **DevicePort** (`camera.capture`, `camera.frame_available` already exist in `DEVICE_CONTRACT.md`).
- **Local first:** presence and face detection run **on the Pi CPU** (OpenCV YuNet / MediaPipe class detectors at low
  resolution, a few frames per second). Frames never leave the device for these features.
- **Cloud only on request:** "מה זה?" / "תקרא לי את זה" take **one still photo** and send it to the backend vision model
  (currently `gpt-6-luna`: input text+image, $0.10 / 1M input tokens), then GPT-Live speaks the answer. No streaming video
  to the cloud.
- **Important finding:** the current voice model **GPT-Live 1 does not accept images** (OpenAI model page lists image and
  video as unsupported, checked 2026-10-19). `gpt-realtime-2.1` does accept image input. So vision must go through the
  existing backend delegation path (GPT-6 via Responses), not into the GPT-Live session, unless we switch voice engine.
- **AI accelerator not needed for phases 1-3.** Re-evaluate the AI Camera (IMX500) or AI HAT+ only if CPU detection
  measurably hurts voice latency or the Godot face frame rate on the Pi.

## 2. Use cases (ranked by value / risk)

| # | Use case | What happens | Processing | Risk |
|---|---|---|---|---|
| 1 | **Presence + greeting** | Someone sits in front of JARVES -> face wakes, eyes turn to them, optional "ערב טוב" | Local face/person detection, no identity | Low |
| 2 | **Look at the person** | Eyes follow the speaker's face position while talking | Local, bounding box -> gaze | Low |
| 3 | **"מה זה?"** | User holds an object up; one photo -> vision model -> spoken answer | Cloud, single still, on explicit request | Medium (image leaves home) |
| 4 | **Read text / documents** | "תקרא לי את המכתב הזה" -> full-res still (autofocus) -> OCR + summary in Hebrew | Cloud vision (Hebrew OCR quality is good in current GPT models; measure) | Medium |
| 5 | **Recognize family** | "שלום נועה" instead of a generic greeting; per-person memory/privacy (guest mode for email) | Local face embeddings, **opt-in per person**, stored only on device | High (biometric data) |
| 6 | **Gestures** | Raise palm = stop/mute, thumbs-up = yes for confirmations | Local hand landmarks | Medium (false triggers) |
| 7 | **Security / "who was home"** | Motion snapshots while away | **Not recommended**: turns a companion into a surveillance device; only if the user explicitly asks later | High |

## 3. Privacy-first design

1. **Off by default.** The camera Skill and presence detection are disabled until the user turns them on in the dashboard.
2. **No recording by default.** Frames live in RAM only; detection output is a box + confidence, not an image. No frame is
   written to disk or logs. Debug snapshots need an explicit, time-limited dashboard switch.
3. **Physical shutter** (printed slider over the Ø8 mm camera window, CAD `_CameraWindow`). This is the only control we can
   honestly call a guarantee. The software "camera off" is labelled as software, as DEVICE_CONTRACT requires for mute.
4. **Indicator:** whenever the sensor is streaming, the face shows a small camera glyph and (when the LED ring exists) a
   dedicated LED segment lights. Camera Module 3 has **no built-in activity LED**, so the indicator is software-driven;
   the shutter covers the trust gap. A hardware-tied LED (powered from the camera's supply rail) is a possible #8
   electronics improvement, UNVERIFIED feasibility on the CSI connector.
5. **Cloud sends are visible and audible:** a shutter sound/face flash at capture, and JARVES says what it sent
   ("צילמתי ושלחתי לבדיקה"). Only the single requested still is uploaded; OpenAI API data is not used for training by
   default (re-check policy at implementation).
6. **Face recognition opt-in per person**, adults enroll themselves; children only with parent consent. Embeddings
   (not photos) stored in `<state_dir>/faces.db`, deletable per person ("תשכח את הפנים שלי"). Never uploaded.
   (Israeli Privacy Protection Law treats biometric data as sensitive; keep it local and minimal.)
7. **Guests:** an unknown face means "guest mode": no private calendar/email read-outs.

## 4. Hardware options for Raspberry Pi 5

| Option | What | List price (vendor) | Fit with CAD | Pros | Cons | Verdict |
|---|---|---|---|---|---|---|
| **Camera Module 3 Wide** (12 MP IMX708, 120 deg, PDAF autofocus, HDR) | Already E03 in R5b | ~$35 / £24+ (std); Wide a bit more | **Modelled** (25 x 24 x 12.4); bosses not yet modelled | Autofocus for reading documents; wide view for presence; official libcamera support | No on-chip AI; no LED | **Recommended** |
| Camera Module 3 standard (75 deg) | Narrower lens | ~$25 | Same footprint | Sharper at distance | Misses people beside the robot | Alternative |
| **Raspberry Pi AI Camera** (Sony IMX500 + RP2040, on-sensor inference) | Runs small detectors on the sensor, outputs tensors via picamera2 | $70 launch, ~$77 Adafruit | Similar 25 x 24 mm, different depth/lens: **re-check CAD** | Detection costs ~0 Pi CPU | Manual focus (poor for documents), ~78 deg FOV (UNVERIFIED), small model set | Phase 5 option if CPU is short |
| **AI HAT+ (Hailo-8L 13 TOPS / Hailo-8 26 TOPS)** | PCIe NPU HAT | ~$70 / ~$110 | Stacks on the Pi in the head: **heat, height and ribbon routing UNVERIFIED** | Fast multi-model vision | Extra heat/power in a closed head; cost | Not needed |
| **AI HAT+ 2 (Hailo-10H, 40 TOPS INT4, 8 GB RAM)** | Also runs small LLM/VLM locally | £172.80-£192 (The Pi Hut) | Same concerns, more heat | Local VLM fallback | Expensive; local VLM far below cloud quality | Not needed |
| USB webcam | Already used for dev on Windows | - | Fallback A' only (R4-4) | Zero integration on PC | USB load, bulk | Development only |

CAD alignment (#8, `docs/assembly/ASSEMBLY_GUIDE.md` step 4.2, `cad/build_pebble.py` `_CameraWindow`, `cad/export_meshes.py`):
camera bosses are **not modelled yet** ("vendor hole pattern"). For the next CAD round: model 2 x M2 bosses from the
official Camera Module 3 drawing, check the 500 mm ribbon bend radius through the head, add the shutter slot, and
confirm the window gives an unobstructed 120 deg cone (no display bezel in view). If the AI Camera is ever chosen,
re-check its depth and lens position before printing.

## 5. Software architecture

```
Pi camera (CSI) --picamera2--> CameraAdapter (DevicePort side, the only code that imports picamera2/OpenCV capture)
     |  low-res stream 320x240 @ 5 fps                       | full-res still on request (autofocus trigger)
     v                                                        v
PresenceDetector (local: YuNet / MediaPipe face+person)   VisionSkill.look(question)
     |  presence.changed, face.box(x,y,w,h)                   |  JPEG in RAM -> backend vision (gpt-6-luna)
     v                                                        v
Core (app.py): wake/greet, guest mode, gaze target       spoken answer via GPT-Live delegation
     |
     v  face.set_gaze(x,y) / face.set_state(...)  (WebSocket to Godot)
Godot face: eyes look toward the person (bounded, smoothed)
```

- **Libraries:** `picamera2` (Raspberry Pi's official Python library on libcamera); OpenCV `FaceDetectorYN` (YuNet) or
  MediaPipe Tasks face detector for presence; MediaPipe hand landmarker for gestures; OpenCV `FaceRecognizerSF` (SFace) or
  an ArcFace-class ONNX model for opt-in recognition. Pick versions at implementation time and record them
  (AGENTS.md best-in-class rule). All local models are **fallback-grade by design** and only drive presence/gaze.
- **Cloud vision options compared (2026-10-19):**

| Model | Image input | Use | Note |
|---|---|---|---|
| `gpt-6-luna` (Responses) | yes (text+image in, text out), $0.10 / $0.50 per 1M | **Default** for "what is this" / reading | Already our backend; one photo costs well under 1 cent |
| `gpt-6.1-sol` | check model page | Hard documents | Slower, more expensive |
| `gpt-realtime-2.1` | yes (image input, $5 / 1M image tokens) | If the voice engine moves to Realtime, images can go straight into the voice session | Not our current voice engine |
| `gpt-live-1` | **no** (image and video unsupported) | - | Current voice engine |
| Gemini Live (3.1 Flash Live, preview) | video as ~1 fps JPEG frames | Continuous "look with me" | Different vendor; preview; only with a measured quality reason |

- **Tools exposed to the model** (Skill, permission `read`, but camera use itself requires the dashboard opt-in):
  `camera_look(question: str)` -> one still -> backend vision -> `ToolResult(message=<Hebrew answer>)`;
  `camera_read_text()` -> full-res autofocus still -> transcript + short summary.
  Both refuse with `CAMERA_DISABLED` / `CAMERA_SHUTTER_CLOSED` (detected by a near-black frame) and say so in Hebrew.

## 6. Godot face integration (look at the person)

- Godot already has a gaze vector (`godot/face/face.gd`: `_look`, state look + saccade + drift, sent to the shader).
  Add one inbound message `face.set_gaze {x, y, confidence}` (normalized -1..1 from the face box centre, mirrored for the
  camera's viewpoint). The face blends toward it with the existing exponential smoothing; on low confidence or no person
  for 2 s it returns to its idle look.
- Keep the **calm, natural** behaviour the user asked for: cap gaze offset (e.g. 25 % of radius while tracking), limit
  update rate (5 Hz input, smoothed at 60 fps), no jitter. The existing idle test (`face_app.gd`, idle gaze < 4 %) stays
  as is because tracking is a separate state.
- Presence also drives `face.set_state` (sleep -> idle when someone arrives) and can replace the wake word for greeting
  only, never for listening (mic privacy stays with the wake word / button).

## 7. Phased plan with tests and cost

| Phase | Scope | Tests (all headless, no real camera in CI) | Cost |
|---|---|---|---|
| **C0** CAD (#8) | Bosses, ribbon path, shutter slot, window cone check | CAD check scripts (`cad/check_pebble.py`), render | 0 |
| **C1** Adapter + fake | `CameraAdapter` interface; `FakeCamera` replaying fixture JPEG/PNG frames; Windows USB webcam adapter for dev | unit tests with fixture frames; adapter-absent -> recoverable state, not crash | 0 |
| **C2** Presence + gaze | Local face detection -> `presence.changed`, `face.set_gaze`; Godot smoothing | fixture images with/without faces (public-domain or synthetic, **no family photos in git**); Godot headless gaze test (bounded, returns to idle) | 0 |
| **C3** "מה זה" / read text | `VisionSkill` via backend vision; capture sound + face cue | fake backend in CI; 2-3 manual real calls on fixture photos (< $0.01 total) | ~$0.001 per photo (estimate: ~1k image tokens at $0.10/1M plus output) |
| **C4** Pi bring-up | Camera Module 3 Wide on Pi 5: picamera2, autofocus stills, CPU load and voice-latency impact, Godot fps | on-device measurements; **hardware gate, not closable by mocks** | Camera ~$35-40 + ribbon (ILS UNVERIFIED) |
| **C5** Opt-in family recognition | Enrollment by voice + dashboard, per-person delete, guest mode | embedding match tests on synthetic faces; delete removes all records | 0 |
| **C6** Gestures (optional) | Palm = stop, thumbs-up = confirm (only as a second factor, never alone for destructive actions) | fixture landmark sequences; false-trigger rate | 0 |
| C7 (only if asked) | Accelerator (AI Camera / AI HAT+) if C4 shows CPU contention | same tests + thermals | $70-$190 |

## 8. Risks

| Risk | Mitigation |
|---|---|
| Camera feels creepy / trust loss | Off by default, shutter, visible indicator, no recording, spoken notice on every cloud send |
| CPU load on the Pi hurts voice latency or face fps | Low-res, 5 fps, pause detection while speaking if needed; measure in C4; accelerator only if measured |
| Heat in the closed head (Pi + display + camera) | Already an open #8 thermal risk; camera adds little, accelerators add a lot |
| Wide lens distortion hurts document reading | Ask the user to hold the page closer; autofocus still at full res; crop centre |
| Face recognition errors (twins, kids growing, lighting) | Use only for greetings/guest mode, never for security or permissions |
| Biometric data law | Local only, opt-in, deletable, no upload |
| Prompt injection via images (text in a photo saying "ignore instructions") | Same rule as email: image text is untrusted data; no action tools in the same turn without confirmation |
| Ribbon damage / bend through the head | 500 mm ribbon, bend radius check in CAD, service lid access (assembly guide) |
| Model/vendor changes (GPT-Live gains vision, Realtime upgrade) | Vision behind one Skill; switch backend without changing tools; re-check at each milestone |

## 9. Open questions for the user

| Question | Recommendation | Status |
|---|---|---|
| Want the camera at all in v1? | Yes for presence/gaze and "what is this", off by default | USER DECISION REQUIRED |
| Family face recognition? | Later (C5), opt-in per person | USER DECISION REQUIRED |
| Physical shutter in the next CAD round? | Yes, small printed slider | USER DECISION REQUIRED (appearance) |
| Security/away-mode snapshots? | No | USER DECISION REQUIRED |
| Buy Camera Module 3 Wide now? | No purchase until #8 purchase gate; check Israeli stock/price | DEFERRED to #8 |

## Sources (checked 2026-10-19)

- GPT-Live 1 model page (input audio/text; image and video unsupported): <https://developers.openai.com/api/docs/models/gpt-live-1>
- GPT-Realtime-2.1 model page (image input supported): <https://developers.openai.com/api/docs/models/gpt-realtime-2.1>
- GPT-6 Luna model page (text+image input, pricing): <https://developers.openai.com/api/docs/models/gpt-6-luna>
- OpenAI model list: <https://developers.openai.com/api/docs/models/all>
- gpt-realtime image input announcement: <https://openai.com/blog/introducing-gpt-realtime/>
- Raspberry Pi AI HAT+ 2 (Hailo-10H) listing: <https://thepihut.com/products/raspberry-pi-ai-hat-2>
- Raspberry Pi AI Camera (IMX500) launch: <https://cnx-software.com/?p=138368>, retail <https://adafruit.com/product/6009>
- Gemini Live API (video frames ~1 fps): <https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api>
- Camera Module 3 dimensions: `cad/ASSUMPTIONS.md` (verified from raspberrypi.com docs earlier)
