# JARVES — Unified Product Vision
> Product vision agreed with the owner on 20 October 2026. The repository is named JARVIS; the product name in current project docs is JARVES.

## חזון / Vision

**JARVES הוא עוזר אישי אחד — עם אותו מוח, אותה זהות, אותו זיכרון ואותן יכולות — שנגיש דרך מחשב, טלפון, רכב, טלפון המדמה רובוט, ולבסוף רובוט פיזי בסלון.**

The goal is **one evolving assistant, not six separate products**. Start with the already-working Windows prototype, then gain mobility and test real device interactions *before* committing to dedicated physical hardware. Move through the stages incrementally; each stage should add an adapter or interaction surface rather than fork the core product.

This is a long-term **product direction**, not authorization to implement all six stages. Existing `docs/NOW.md`, milestone gates, issue scopes, and physical purchase approvals remain authoritative. Proposed order may be refined based on feasibility, without losing the distinct goals.

## Six product stages / ששת השלבים

| Stage | Experience and outcome | Boundary / evidence before moving on |
| --- | --- | --- |
| **1 — Desktop assistant** | Stabilize and finish the existing Windows JARVES experience: Hebrew conversations, face/UI, persistent memory, Skills, diagnostics, and repeatable tests. Continue the approved software plan. | Desktop end-to-end flow works and outstanding defects are documented; do not claim every integration is finished until tested. |
| **2 — PC-hosted personal server + phone access** | Keep the PC running the JARVES Core. Connect from a phone to talk to the **same assistant**, read relevant state and ask it to run allowed actions/Skills. Start from a secured mobile-friendly client/dashboard; remote-over-internet access is a separate security design and approval. | Authenticated pairing, transport, reliable voice interaction, clear connection/offline states, and no duplicate per-device memory. |
| **3 — Android Auto / car interaction** | While driving, access the same assistant through the phone/car audio for hands-free questions and allowed tasks such as requesting music playback. **Never** vehicle motion/control. | Research current official Android Auto app-category, microphone, audio, background, and distribution restrictions; prototype only supported/compliant surfaces. Steering-wheel assistant-button replacement, car UI integration, and direct playback control are **not promised**. A phone/Bluetooth voice path may be the practical fallback. |
| **4 — Everyday Android assistant app** | Install a native Android companion on the user's ordinary phone for daily personal-assistant interactions, voice, notifications and relevant device-integrated Skills. It uses the same identity, memory and permissions as desktop. Determine which features must stay on the PC and which may work locally. | Functional on-phone UX with explicit OS permission gates, lifecycle/background restrictions, secure sync, and truthful offline behavior. Stage 4 is a **personal mobile assistant**, not yet a dedicated robot emulator. |
| **5 — Phone as a robot hardware stand-in** | Install/use JARVES on a spare phone (candidate: OnePlus 11) fixed in a dock or kiosk-like setup to **behave as the future stationary home robot**. Its display acts as the face; microphone, speakers, camera and supported sensors are test hardware. Validate wake/listening/voice, face synchronization, camera/privacy state, reconnect/restart, heat/charging and around-the-room usability under realistic conditions. Reuse Core, Skills and DevicePort contracts; **do not build a separate brain**. | A documented capability matrix showing what the phone has actually validated and what remains unverified for Raspberry Pi hardware (microphone array, power/mute circuitry, acoustics, cooling, physical fit). Phone is a functional prototype, **not** a literal 1:1 physical replica. |
| **6 — Dedicated physical JARVES** | Move the validated behavior into the stationary, friendly, serviceable 3D-printed companion with its intended display, microphone system, speakers, optional camera, controls and Linux/Pi-class compute. Carry forward shared contracts and lessons from stage 5; replace device adapters rather than reimplement memory/assistant behavior. | Owner-approved industrial/visual design, sourced components, printable and repairable enclosure, physical fit/safety/acoustic/thermal/privacy tests, beginner-friendly assembly instructions, and explicit hardware/purchase gates. |

## Non-negotiable cross-device invariants

1. **One logical identity and memory.** Conversations, preference/profile memory and Skills should be consistent across authenticated devices. If several Core processes eventually run, specify one source of truth and deliberate synchronization/conflict handling; never silently create independent users or memories.
2. **Reuse, do not rewrite.** Keep Python Core/orchestration, AI provider and Skills contracts, device event semantics and appropriate face/state schema. Implement thin device-specific clients/adapters, and evolve `DevicePort` only when a tested requirement demands it. Current Godot/FreeCAD/Blender source-of-truth boundaries remain intact.
3. **Server first, local later when justified.** In early stages the PC hosts the assistant; later a phone or physical device may run an approved Core subset or Core itself. Whether the Core is centralized, replicated, or moved is an **architecture decision to validate** — not a requirement to run five concurrent brains. An unavailable PC must be reported clearly, not masked as working local AI.
4. **Device-aware capabilities.** Declare supported actions per surface: desktop, phone, driving/car, stationary phone, robot. Do not assume Android Auto permits ordinary Android app behavior or that phone APIs replicate physical switches.
5. **Security and consent.** Authenticated pairing, per-device permissions, minimal scopes, secure credential handling, transport protection, explicit confirmation for consequential actions. Do not expose a PC service to the public internet or silently activate mic/camera.
6. **Privacy and truthful status.** Distinguish software mute from future verified physical microphone mute, and clearly present mic/camera/cloud use, connection state and failures. Follow current project safety rules.
7. **Observability and testability.** Each phase gets focused integration tests and an honest capability/status matrix. Mocked success never substitutes for tests on an actual Android phone, compatible car, or physical robot.
8. **Incremental delivery.** No big-bang rewrite, new subscription, physical purchase, installation on a personal phone, or unapproved feature milestone merely because it appears in this vision.

## Suggested architectural shape — not yet an approved implementation design

```text
                  Personal identity + memory + Skills
                              JARVES Core
                                  |
                      authenticated capability API
                                  |
       +--------------------------+----------------------------+
       |             |            |             |              |
   Windows UI    Phone client   Car path    Docked phone     Physical robot
   + simulator    (daily use)   (limited)   (robot stand-in)  (device adapters)
```

In stages 1–3 the authoritative Core is initially PC-hosted. Stage 4 must decide the proper degree of local Android execution. Stage 5 is specifically about real sensor/UI/voice testing as a stand-in for the physical device. Stage 6 replaces stand-in hardware with tested dedicated components. Architecture and networking choices are subject to technical review.

## Current-project compatibility and handoff

- Preserve the active `SW-V2/SW-V3` software work and the existing separate CAD/physical-readiness gates. The repository already proposed a **phone-as-satellite / stand-in** after SW-V3; this vision elevates it to its **own explicit pre-hardware stage**, separate from everyday Android and car use.
- Existing `docs/ROADMAP.md` and `docs/NOW.md` remain the authoritative *execution* roadmap. This file is the **product north star** for planning follow-on milestones and future Issues, **not** a blanket approval.
- Future planning should break stages 2–6 into bounded Issues **only when their scope is selected**, with acceptance criteria, relevant official platform documentation, security and device QA. Do not skip the docked-phone validation before purchasing dedicated robot hardware without a new owner decision.
- Hardware form remains stationary (no motors, driving or vehicle control). Keep DIY assembly practical, printable and locally sourceable in Israel; no purchase authorization is implied.

## Open decisions for later technical research

- PC/LAN-only vs securely mediated internet access and recovery when the host PC is offline.
- Android app distribution, wake word, background microphone/foreground service, permissions and on-device processing limits.
- Android Auto compliant integration and whether music can be handed off to existing phone playback apps.
- Single Core versus selective local phone execution; latency, memory synchronization, cost and failure modes.
- Docked phone power/thermal/battery management and real-room microphone/acoustic tests.
- Hardware-specific privacy switch, camera, microphone array and Raspberry Pi performance remain physically **UNVERIFIED**.

**Success criterion:** A user interacts with **the same JARVES**, recognizes its personality and memory, and can perform allowed actions naturally whether seated at the Windows PC, carrying a phone, speaking hands-free in the car, facing a docked phone prototype, or eventually standing in front of the dedicated robot.
