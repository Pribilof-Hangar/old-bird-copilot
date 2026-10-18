# Release checklist: Old Bird Copilot 1.4.2

Only tests that were actually run are marked done. **Kind** says how each was checked:
auto = automated tests, sim = simulated or off-screen check, real = done on a real Windows PC, flight = real MSFS 2024 flight.

## Build

| Check | Kind | Result |
|---|---|---|
| `build.ps1` builds the installer and portable zip from the source | real | ✅ 2026-10-17 |
| 189 unit tests (callout logic, descent planner, settings, recorder, voice helpers) | auto | ✅ |
| Built exe reads its built-in airport list (frozen check in `build.ps1`) | real | ✅ |
| Windows Defender scan of the exe and the voice folder | real | ✅ no threats |
| No developer paths, secrets or test data in the release | review | ✅ exe, voice, guide only; default settings are generated at build time |

## Self-contained (no dependencies)

| Check | Kind | Result |
|---|---|---|
| Exe bundles Python, tkinter, pywin32, the MSVC runtime and SimConnect.dll | review (archive listing) | ✅ |
| Voice engine bundles its MSVC runtime DLLs (not taken from the system) | review (PE imports) | ✅ |
| No references to SPAD.next, FSUIPC, Axis and Ohs, MobiFlight, WASM, registry | review (code search) | ✅ |
| Truly clean Windows 11 (no Python, no MSVC runtime) | real | ⬜ **not done**: needs a virtual machine (Windows 11 Home has no Sandbox) |

## Robustness

| Check | Kind | Result |
|---|---|---|
| Joe voice from a folder with special characters ("Jösé Ñandú Документы") and such a TEMP | real | ✅ |
| Settings and flight recordings in such a folder | real | ✅ |
| Damaged or garbage settings file: app starts on defaults and keeps a backup | auto | ✅ |
| First start creates the settings quietly | auto | ✅ |
| Window fits: 1080p at 100/125/150 %, 1440p, 1600p at 175 %, 4K at 200 % | sim | ✅ (1366×768 doesn't fit: known limitation) |
| Settings and calculator windows fit on those screens | sim | ✅ |
| Second launch shows "already open" instead of a second copy | real | ✅ installed 1.4.0: the second copy showed its message and exited; one copy and one Joe kept running |

## Installed release (on the developer's MSFS PC)

| Check | Kind | Result |
|---|---|---|
| Fresh install, no admin rights ("Administrative install mode: No"), Start menu + desktop shortcut, Settings > Apps entry "Old Bird Copilot 1.4.x by Old Bird Copilot" | real | ✅ 2026-10-17 |
| First launch from the Start menu: window, Joe starts from the install folder, settings created in %APPDATA%\Old Bird Copilot, no error log | real | ✅ |
| Clean shutdown: app and Joe processes stop, no temp folders left | real | ✅ |
| Update 1.4.0 → 1.4.1 over the top: one Settings > Apps entry, settings (marker Vref 123) and recordings kept | real | ✅ |
| Update 1.4.1 → 1.4.2 over the top: one entry, settings kept, LICENSE.txt and voice source links installed, starts with Joe | real | ✅ |
| Setup run while the app is open detects it ("Please close all instances…") and changes nothing | real | ✅ (unattended run: refused with exit code 1) |
| Uninstall (silent) removes program, shortcuts and Settings > Apps entry; keeps settings and recordings | real | ✅ |
| Reinstall after that picks the kept settings up again | real | ✅ |
| Interactive uninstall answering "Yes, also remove my settings and flight recordings" removes the data folder too | real | ✅ (buttons clicked by script after reading each dialog's text) |
| Portable zip from a folder with spaces: runs with Joe, keeps settings next to the exe, doesn't create the per-user folder | real | ✅ |
| Setup wizard clicked through by hand (pages, wording, desktop-shortcut tick box) | real | ⬜ installs were run unattended (/SILENT); a manual click-through is still to do |

## With MSFS 2024 (Microsoft Store version, PMDG DC-6)

| Check | Kind | Result |
|---|---|---|
| Connects, reconnects after the sim restarts, survives a sim crash ("pipe broken" 2026-10-10) | flight (earlier versions) | ✅ |
| Callouts, DC-6 mode, descent planner, landing check on real flights | flight (1.3.x) | ✅ |
| A full flight with the installed 1.4.1 | flight | ⬜ |
| Steam version of MSFS 2024 | | ⬜ not available for testing |

## Known limitations

- Not code-signed: SmartScreen shows "Windows protected your PC" on first run.
- Screens smaller than about 1080 px tall (e.g. 1366×768) can't show the whole window.
- Windows 10 not tested (the installer allows it; the guide only promises Windows 11).
- Licence: GNU AGPL-3.0 (decided 2026-10-17). The source must be public (GitHub) when releases are shared; LICENSE.txt ships with the app.
