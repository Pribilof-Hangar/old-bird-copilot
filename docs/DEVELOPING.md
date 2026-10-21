# Old Bird Copilot: developer guide

For building and releasing the app. End users never need any of this; their guide is `docs/Read Me First.txt`.

## What it is

A standalone Windows companion app (Python 3.13, tkinter). It reads Microsoft Flight Simulator 2024 through
SimConnect (read-only: it never sends commands to the sim) and speaks callouts with a bundled offline voice.
Nothing is installed into the simulator or its Community folder.

| Part | Where |
|---|---|
| Callout logic (pure, no sim/audio) | `landing_callouts/engine.py`, `descent.py`, `vref.py` |
| Sim reading (SimConnect) | `landing_callouts/sim.py` |
| Voice: Michael (Kokoro am_michael via kokoro-onnx, in-process, 2 CPU threads, fixed calls pre-rendered) with Windows SAPI fallback | `landing_callouts/speech.py`, `voice/` |
| Window | `landing_callouts/gui.py` |
| Settings (`config.toml`) | `landing_callouts/config.py` |
| Airport list (OurAirports, public domain) | `landing_callouts/data/airports.csv.gz`, rebuilt with `tools/make_airports.py` |

## Data locations

- **Installed app:** settings, flight recordings and the error log are in `%APPDATA%\Old Bird Copilot`.
- **Portable zip:** it ships with a `config.toml` next to the exe, so that copy keeps everything in its own folder.
- **From source:** `config.toml` in the current folder or the checkout.

## Setup (once)

1. Python 3.13 (64-bit) from python.org.
2. In the project folder:
   ```
   py -3.13 -m venv .venv
   .venv\Scripts\python -m pip install -e . pyinstaller
   ```
3. Inno Setup 6: `winget install JRSoftware.InnoSetup`.
4. The `voice/` folder is not in the repo (too big). Build it once:
   - `voice/kokoro/kokoro-v1.0.fp16.onnx` (or the smaller `kokoro-v1.0.int8.onnx`) from
     https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0
   - `voice/kokoro/michael.bin`: only the am_michael entry of `voices-v1.0.bin` from the same release
     (`np.savez(path, am_michael=np.load("voices-v1.0.bin")["am_michael"])`, then rename to michael.bin)
   - `voice/espeak/`: `espeak-ng.dll` and `espeak-ng-data/` from the installed `espeakng_loader` package (delete every
     `*_dict` except `en_dict`), plus `msvcp140.dll`, `vcruntime140.dll`, `vcruntime140_1.dll` from System32
   - `voice/THIRD-PARTY-NOTICES.txt`

## Run and test

```
.venv\Scripts\python -m landing_callouts              # the window
.venv\Scripts\python -m landing_callouts --no-sim     # without MSFS
.venv\Scripts\python -m unittest discover -s tests    # all tests
.venv\Scripts\python -m landing_callouts --replay flights\flight-....csv   # re-run a recorded flight
```

Every flight is recorded to `flights/` (the newest 20 are kept). Replaying a recording is the main way to
reproduce and fix a bug reported from a real flight.

## Build a release

1. Bump `__version__` in `landing_callouts/__init__.py` and `version` in `pyproject.toml`.
   Edit with an editor, not PowerShell `Set-Content`, which damages non-ASCII characters.
2. `powershell -ExecutionPolicy Bypass -File build.ps1`
   This runs the tests, builds `dist\OldBirdCopilot.exe` with PyInstaller, checks the exe can read its airport
   list, builds `release\OldBirdCopilot-Setup-<v>.exe` (Inno Setup) and `release\OldBirdCopilot-<v>-portable.zip`.
3. Work through `docs/RELEASE_CHECKLIST.md` on the built installer, not the source.

### Installer facts (`installer/OldBirdCopilot.iss`)

- Per-user install (`PrivilegesRequired=lowest`) into `%LOCALAPPDATA%\Programs\Old Bird Copilot`. No admin prompt.
- Start menu shortcut, optional desktop shortcut, entry in Settings > Apps.
- The `AppId` GUID must never change: Windows uses it to match updates and the uninstaller.
- `AppMutex=LandingCallouts.SingleInstance` (the app's single-instance mutex): setup and uninstall ask the user
  to close a running copy.
- Uninstall asks before removing `%APPDATA%\Old Bird Copilot`. The default is to keep it.

## Signing and SmartScreen

Releases are **not code-signed**, so Windows SmartScreen shows "Windows protected your PC" on first run. The user
guide explains "More info > Run anyway" honestly; never tell users to disable antivirus or SmartScreen.
Options if that changes: Azure Trusted Signing (about $10/month, eligibility rules apply) or an OV code-signing
certificate (roughly $200-400/year). Signing the installer and the exe removes the "unknown publisher" warning;
SmartScreen reputation still builds over time.

## Third-party components

| Component | Licence | Notes |
|---|---|---|
| Python, tkinter (bundled by PyInstaller) | PSF | |
| pywin32 | PSF | |
| Python-SimConnect 0.4.26 | AGPL-3.0 | bundled into the exe; compatible with the app's own AGPL-3.0 |
| SimConnect.dll | Microsoft (MSFS SDK) | the SDK permits redistribution |
| Kokoro-82M model + am_michael voice | Apache-2.0 | `voice/kokoro/` |
| kokoro-onnx | MIT | in the exe |
| ONNX Runtime | MIT | in the exe |
| phonemizer | GPL-3.0 | in the exe; GPL-3.0 is compatible with the app's AGPL-3.0 |
| eSpeak NG (espeak-ng.dll, data) | GPL-3.0 | `voice/espeak/`, loaded in-process (same compatibility) |
| MSVC runtime DLLs | Microsoft redistributable | |
| OurAirports data | public domain | |

**Licence:** Old Bird Copilot is licensed under the **GNU AGPL-3.0** (`LICENSE`, decided 2026-10-17). That also
covers the bundled AGPL-3.0 Python-SimConnect. Every release must keep the source available (the public GitHub
repo) and ship `LICENSE.txt` with the app (the installer and zip do). The voice components' source links and
offer are in `voice/THIRD-PARTY-NOTICES.txt`.
