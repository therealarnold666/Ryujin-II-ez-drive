# Package audit

Audit date: 2026-08-09

## Included current state

- Current static background: `ryujin_lcd/assets/background.png`
  - SHA-256: `82258A2BE4652CFC5052FA2F8E6A9ACB4A8C716990E7BBD24E8E03B2C3C654D4`
- ROG render font: `ryujin_lcd/assets/ROGFonts-Regular_0.otf`
  - SHA-256: `2C5C3CCCA9A8992C37D89C936E82CB0435F6088F4AAC9EA2C0617CDCA2EE6CC1`
- Current 320x240 three-band layout, enlarged CPU/GPU labels and values, symmetric four-column spacing, `°C`, GHz, V, VRAM/RAM absolute bars: `ryujin_lcd/render.py`
- Windows Ryzen boost clock via native PDH processor-performance counter: `ryujin_lcd/sensors.py`
- Animated GIF decoding/cache and 24 FPS scheduling: `ryujin_lcd/render.py`, `ryujin_lcd/engine.py`
- Windows direct HID + WinUSB backend and mode recovery:
  - Source: `native/ryujin_direct_helper.cpp`
  - Binary: `native/ryujin_direct_helper.exe`
- Windows sensor helper:
  - Source: `native/sensor_helper.cs`
  - Binary/dependencies: `native/sensors/`
- Armoury Crate/AIDA64 conflict handling: `ryujin_lcd/conflicts.py`, `scripts/check-conflicts.ps1`
- Ubuntu conflict handling and access rules: `scripts/check-conflicts.sh`, `scripts/99-ryujin-lcd.rules`
- Ubuntu parity diagnostic: `scripts/check-ubuntu-parity.sh`
- Reproducible Windows executable recipe: `RyujinLCD.spec`
- Login startup entry point with conflict-safe automatic LCD start: `--auto-start --no-browser`
- Reproducible Windows logon task installer: `scripts/install-autostart.ps1`

The source tree, Python wheel and PyInstaller build were checked to contain both visual assets. The installed Windows copy has the same background SHA-256 as the active per-user background.

## Ubuntu 26.04 parity boundary

The Python renderer, assets, frame format and LCD command sequence are shared. The Linux backend now mirrors the verified Windows sequence: status query, live-mode selection, per-frame prepare, USB bulk frame, HID acknowledgement and five-second mode recovery.

Physical LCD parity is not certified yet because this workstation has no Ubuntu/WSL distribution or container runtime, and WSL without full composite USB passthrough would not be a valid device test. Run `scripts/check-ubuntu-parity.sh`, followed by a real static and 24 FPS playback test on Ubuntu 26.04 bare metal.

CPU voltage is not guaranteed to match Windows: the Windows 9800X3D value uses AIDA64's registry export, while standard Linux `k10temp` normally does not expose an equivalent voltage channel.
