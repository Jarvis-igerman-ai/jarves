# Hardware plan — future stage only (no purchasing now)

Candidate platform: Raspberry Pi 5, 8GB, Raspberry Pi OS Desktop 64-bit. Connections previously discussed:
- 5" 1080×1080 round display: micro-HDMI for video + USB for touchscreen/power per final SKU.
- Seeed ReSpeaker XVF3800 in **USB audio firmware/mode**: Pi/Windows USB audio input and audio output; actual audio connector variants MUST match purchased board.
- Amplified speakers: audio from ReSpeaker output, powered as specified by speaker model; no bare PAM8403 mandatory.
- Camera: Raspberry Pi Camera Module 3 Wide via MIPI/CSI + matching Pi 5 ribbon; Windows interim uses ordinary USB camera instead.
- Button: GPIO17/GND candidate on Pi, internal pull-up, `NO` momentary switch. Windows interim: USB GPIO bridge (if required); visual simulation uses UI toggle. Never wire GPIO to 5V.
- Presence: optional mmWave/PIR; light: optional I2C lux sensor; power/mute status and real privacy switch require electrical validation.
- Pi 5 USB 2.0 x2, USB 3.0 x2, dual micro-HDMI, MIPI connectors, GPIO, PCIe; leave room for a powered USB hub and SSD. **Port count not equal to power capacity**.
- SSD optional later (Pi USB3 / PCIe depending on accelerator conflicts). Reserve space; don't buy an AI HAT unless specific model, compatibility and thermal/power tests justify it.
- First build: stable mains electricity. Battery/UPS only future optional engineering.
- On Pi: systemd autostart, remote SSH/Tailscale, optionally Pi Connect; avoid internet-facing open ports.

### Windows interim stage
First connect the real ReSpeaker USB array and real powered speakers to Windows, then test genuine microphone pickup, AEC, stereo/music, USB camera, and later sensor USB bridge. Reuse unchanged Core and face contract; only IO adapters and config swap.

### Unverified
Electrical power distribution, specific speaker driver quality, enclosure heat/noise, ReSpeaker firmware for chosen SKU, actual audio output wiring, Pi GPU face frame rate, selected camera FOV and screen fit. Must verify before printing/buying.
