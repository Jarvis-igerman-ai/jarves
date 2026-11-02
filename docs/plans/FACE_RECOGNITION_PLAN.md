# Plan: recognize the owner by face (local enrollment), optional friends (issue #358)

> **Plan only.** No code, no camera access, no face data, no model downloads. Nothing here is authorized
> until `docs/NOW.md` says so. Implementation stage: **not started / UNVERIFIED**. Sources checked **2026-11-02**
> (search results plus the pages linked in section 11; re-read the live pages before any implementation task).
> Builds on [CAMERA_SETUP](../CAMERA_SETUP.md) and the historical [CAMERA_PLAN](CAMERA_PLAN.md). Related: #355 (camera test), #336.

## 1. Goals and non-goals

Goals
1. JARVES can tell "this is the owner" from the local camera, so it can greet him and personalize. It never replaces
   Gate/approvals (a recognized face is never an approval).
2. The owner enrolls himself from the webcam, fully local.
3. Optional, later: a few friends, one by one, each with explicit consent.

Non-goals: identifying strangers, bulk import of a photo library, cloud face matching, video streaming to the cloud,
using a face as a login or payment factor, any action when the camera is off or the shutter is closed.

## 2. Privacy and consent (hard rules)

- Feature id `face_recognition`, **OFF by default**, stage **beta** until verified on a real device.
- **Explicit owner gate for the camera**: enabling needs the camera feature (CAMERA_SETUP) and then this feature
  separately, with a visible consent text (EN+HE). No silent enable by wizard, update or model request.
- **Embeddings only.** Enrollment captures several frames in memory, computes a numeric embedding, keeps the embedding
  and a label, and discards the frames immediately. No photo, crop or video is written to disk, logs, diagnostics or
  backups, or sent anywhere. Diagnostic export contains counts only, never vectors.
- Templates are encrypted at rest under the JARVES home with the existing secret-key mechanism; excluded from
  `jarvis backup` by default (decision D5).
- **Delete-all** and per-person delete: `jarvis face delete --all`, `jarvis face delete NAME`, plus a dashboard button.
  Disabling the feature offers (default yes) to wipe templates. A test proves no template file remains.
- Recognition runs only while the camera is explicitly active (indicator on, privacy shutter open), rate-limited, local only.
- Friends are data about other people: consent comes from that person (section 4) and is recorded with a date.
  Face embeddings are biometric data under privacy regimes such as GDPR; home personal use is the assumed scope
  (decision D1). The feature must never ship enabled by default.

## 3. Threat model

| Threat | Risk | Mitigation |
|---|---|---|
| Photo or screen replay of the owner | False "owner" | A match is a **low-trust hint only**: it never unlocks Gate, approvals, payments, release or secrets. Optional liveness (blink or head-turn challenge) is a later stage and still not a security boundary |
| Stranger enrolls as a friend | Wrong person trusted | Enrolling anyone needs the owner's authenticated local session plus on-screen confirmation; a voice request alone cannot enroll |
| Template theft | Biometric leak | Encrypted at rest, never exported or logged, treated as sensitive even though embeddings are not photos |
| Model update changes embeddings | Silent mismatch | Template stores model id and version; mismatch disables matching and asks to re-enroll |
| False accept / false reject | Greets wrong person | Conservative threshold, N consecutive matching frames, margin over the second-best person; FAR/FRR measured on fakes first, then on the owner's device |
| Prompt injection via image text | Model-driven action | Result is a typed local value, not model text; this feature sends no image to any cloud model |
| Camera active unexpectedly | Privacy | Camera gate, indicator, shutter, one audit entry per activation (no images) |
| Photos Picker over-collection | Privacy | Only user-picked photos, never a library scan; photo bytes discarded after embedding |

## 4. Who may enroll whom

- Owner: enrolls himself via dashboard or `jarvis face enroll --self` while in front of the camera.
- Friends: only the owner can start it, and the friend gives consent that the owner records (name, date, how consented).
  No minors unless the owner confirms guardian consent.
- Each friend is individually listed, deletable and revocable. No auto-discovery and no "unknown person, add?" prompts
  from the camera stream (that would be a stranger-capture path).

## 5. Friends via Google Photos (research, checked 2026-11-02)

- Since 31 Mar 2025 the Library API scopes `photoslibrary.readonly`, `.sharing` and the broad scope no longer work for
  content the app did not upload (403 PERMISSION_DENIED). Apps needing user content must use the **Picker API**
  (`photospicker.googleapis.com`: create a session, the user picks items in Google's own UI, the app reads only those).
  See [updates](https://developers.google.com/photos/support/updates) and the
  [developer blog](https://developers.googleblog.com/en/google-photos-picker-api-launch-and-library-api-updates/).
- **No face-group or people API is exposed**, so "all my friends" cannot be fetched. The only route is per friend: the
  owner picks a few photos of one person, JARVES detects the single face, computes an embedding and drops the bytes.
- Picker needs a Google Cloud OAuth client and sign-in, which is an owner gate. UNVERIFIED: current quotas and whether
  personal use needs consent-screen verification; confirm against live docs in the implementation task.
- Recommended order: friends from the **local camera** first (friend sits in front of it); Picker is stage S6.

## 6. Options compared (best-in-class rule, AGENTS.md)

| Option | Runs | License / terms | Fit |
|---|---|---|---|
| **OpenCV YuNet (detect) + SFace (recognize)** via `cv2.FaceDetectorYN` / `cv2.FaceRecognizerSF` | Local CPU, ONNX, about 40 MB | Apache-2.0 reported (OpenCV Zoo, LocalAI doc); verify model cards at pin time | **Recommended default.** Commercial-safe, fits Pi 5 CPU, same detector class as CAMERA_PLAN. Accuracy below large ArcFace models but adequate for a handful of people at about 1 m |
| InsightFace `buffalo_l` (SCRFD + ArcFace) | Local | Code MIT, **pretrained models non-commercial research only** without a purchased license ([licensing](https://www.insightface.ai/solutions/face-recognition-licensing)) | More accurate, but the license blocks distribution; allowed only as an owner-local experiment |
| MediaPipe Face Detector / Landmarker | Local | Apache-2.0 (verify) | No ready recognition embedder; optional quality or liveness filter only |
| Cloud face APIs (AWS Rekognition, Azure Face, Google) | Cloud | Paid; face data leaves the device; some gated by access programs (UNVERIFIED current terms) | Rejected: conflicts with local-only embeddings and cost caps |
| Vision LLM "who is this" | Cloud | Providers decline identifying people | Rejected |

Runtime target: Raspberry Pi 5 CPU, ONNX or OpenCV DNN, low resolution, 2-5 fps detection, recognition only on stable
faces. These fps figures are targets and **UNVERIFIED** until measured. AI HAT+ or IMX500 is not needed unless CPU load
hurts voice latency. Pinned model files and SHA-256 hashes are recorded at implementation time and fetched only by the
allowlisted setup path (never model-supplied commands). Model id, version and date checked go into the feature entry.

## 7. Design sketch

- Module `face_recognition` behind the existing camera DevicePort (`camera.frame_available`). Pure functions
  `embed(frame)` and `match(embedding, store, threshold)`; store is encrypted data of `{name, embedding, model_id, created, consent}`.
- Output event `face.identity {name|unknown, confidence_band}` with no image; consumers treat it as advisory.
- One lazy tool `who_is_here` returning the typed result only while the camera gate is open (no always-on prompt growth).
- Ships with: CLI `jarvis face status|enroll|list|delete|enable|disable`, guide EN+HE with `[parity.*]`, features row
  (beta, OFF), Hebrew in both places, wizard GROUPS/BUNDLE/SAFE_OFF, `targets()`, DOC_MAP row, changelog fragment.

## 8. Staged rollout (every stage OFF by default, beta)

| Stage | Content | Gate |
|---|---|---|
| S0 | This plan merged; owner decisions D1-D5 | owner |
| S1 | Store, thresholds, match logic, CLI, delete-all, consent text; no camera | none (pure code with fakes) |
| S2 | Camera binding via DevicePort, owner self-enrollment, indicator and shutter honored | **camera owner gate**; Windows webcam first |
| S3 | Real-device acceptance: owner FAR/FRR on his own face, lighting, glasses | owner present at the PC (never closed on mocks) |
| S4 | Pi 5 runtime measurement | **hardware gate** (physical stage) |
| S5 | Friends from local camera with consent records | owner |
| S6 | Friends via Google Photos Picker | **Google sign-in / Cloud project owner gate** |
| S7 | Optional liveness | owner decision |

## 9. Tests without a real camera

- A fake camera port yields synthetic frames; the embedder is a deterministic stub mapping a token to a vector, so
  match, threshold, margin and consecutive-frame logic is tested exactly. No real face images in the repo.
- Required tests: enrollment stores embeddings only (no image bytes anywhere under the home or temp); delete-all leaves
  nothing; model-id mismatch disables matching; feature OFF means the camera is never opened; closed camera gate means no
  frames requested; diagnostics contain no vectors; backup excludes templates; friend enrollment refused without the
  owner session; Picker client tested against a fake HTTP server (session create, poll, expiry, bytes discarded).
- Real-model smoke (S2 onward) runs only on the owner's PC and is recorded as device-level evidence, unless a
  license-compatible public test image exists.

## 10. Owner decisions (open, no default assumed; silence is not approval)

- D1: confirm personal home use only and the consent text (EN+HE) for self and friends.
- D2: approve OpenCV YuNet+SFace as the default model (recommended) vs. buying a license for a stronger model.
- D3: friends from the local camera first (recommended), Google Photos Picker later, or no friends.
- D4: may recognition drive anything beyond greeting and personalization (recommended: never approvals).
- D5: include templates in `jarvis backup` (recommended: no, re-enroll instead).

Owner gates: enabling the camera, enrolling any person, Google sign-in and Cloud project, Pi hardware tests, real-device acceptance.

## 11. Sources (checked 2026-11-02)

- Google Photos API updates: https://developers.google.com/photos/support/updates
- Picker API launch and Library API changes: https://developers.googleblog.com/en/google-photos-picker-api-launch-and-library-api-updates/
- Picker REST reference: https://developers.google.com/photos/picker/reference/rest
- InsightFace model licensing: https://www.insightface.ai/solutions/face-recognition-licensing
- OpenCV Zoo YuNet / SFace (re-check license and model cards at pin time): https://github.com/opencv/opencv_zoo
- LocalAI face recognition doc (lists YuNet+SFace as Apache-2.0): https://localai.io/docs/features/face-recognition/index.html
