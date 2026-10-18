"""Entry point: `landing-callouts` / `python -m landing_callouts`.

No arguments opens the window. --console, --demo, --test-voice and --list-voices run in the terminal.
"""

# Absolute imports so this file also works as the PyInstaller entry script.
from __future__ import annotations

import argparse
import queue
import sys
import time
import traceback
from pathlib import Path

from landing_callouts import APP_NAME, APP_TITLE, __version__
from landing_callouts import config as config_mod
from landing_callouts.app import Controller
from landing_callouts.engine import status_line


def load_config(args, warnings: list[str]) -> tuple[config_mod.Config, Path]:
    """The config and the path it will be saved to."""
    config_mod.warning_handler = warnings.append
    if args.config:
        path = Path(args.config).resolve()
        cfg = config_mod.load(path)
    else:
        found = config_mod.find_config()
        if found:
            path = found
            try:
                cfg = config_mod.load(path)
            except config_mod.ConfigError as e:
                # A damaged settings file shouldn't stop the app: keep it for reference, start on defaults.
                backup = path.with_name(f"config.toml.damaged-{time.strftime('%Y%m%d-%H%M%S')}")
                try:
                    path.replace(backup)
                    kept = f"It was kept as {backup.name}"
                except OSError:
                    kept = "It was left in place"
                warnings.append(f"Your settings file couldn't be read ({e}). {kept} and the default settings "
                                f"are in use.")
                cfg = config_mod.Config(base_dir=path.parent)
                _save_quietly(cfg, path, warnings)
        else:
            path = config_mod.default_config_path()   # first start of the installed app
            cfg = config_mod.Config(base_dir=path.parent)
            _save_quietly(cfg, path, warnings)
    cfg = config_mod.with_overrides(cfg, vref_kt=args.vref, minimums_ft=args.minimums,
                                    landing_target_fpm=args.landing)
    return cfg, path


def _save_quietly(cfg: config_mod.Config, path: Path, warnings: list[str]) -> None:
    try:
        config_mod.save(cfg, path)
    except OSError as e:
        warnings.append(f"Couldn't create the settings file {path} ({e}). Settings won't be kept.")


def run_console(ctl: Controller, until_demo_done: bool = False) -> None:
    """Print events and a status line once a second until Ctrl+C (or the demo ends)."""
    last_status = 0.0
    while True:
        try:
            while True:
                kind, payload = ctl.events.get_nowait()
                if kind == "log":
                    print(payload, flush=True)
                elif kind == "callout":
                    print(f"[SAY] {payload}", flush=True)
        except queue.Empty:
            pass
        s = ctl.latest
        if s is not None and s.t - last_status >= 1.0:
            last_status = s.t
            print(status_line(s, ctl.armed), flush=True)
        if until_demo_done and not ctl.demo_running and ctl.events.empty():
            ctl.speaker.wait_idle(20)
            return
        time.sleep(0.1)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="landing-callouts",
        description="Old Bird Copilot: spoken landing callouts for MSFS 2024, made for classic aircraft. "
                    "With no options it opens the app window.",
    )
    p.add_argument("--config", help="path to a config.toml (default: next to the exe, or the current folder)")
    p.add_argument("--vref", type=int, help="Vref in knots for this flight (enables speed low/high)")
    p.add_argument("--minimums", type=int, help="minimums in ft above ground for this flight")
    p.add_argument("--landing", type=int, help="max touchdown rate in fpm for this flight (e.g. 200)")
    p.add_argument("--console", action="store_true", help="run in the terminal instead of opening a window")
    p.add_argument("--demo", action="store_true", help="fly a fake approach in the terminal, no sim needed")
    p.add_argument("--demo-speed", type=float, default=1.0, help=argparse.SUPPRESS)
    p.add_argument("--test-voice", action="store_true", help='say "fifty, forty, thirty, twenty, retard"')
    p.add_argument("--list-voices", action="store_true", help="print the installed Windows voices")
    p.add_argument("--print", dest="print_only", action="store_true", help="print callouts instead of speaking")
    p.add_argument("--no-sim", action="store_true", help=argparse.SUPPRESS)  # for testing without MSFS
    p.add_argument("--replay", metavar="FILE", help="run a flights/*.csv recording through the callout logic "
                                                     "and compare with what was said")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    if args.list_voices:
        from landing_callouts.speech import list_voices
        for name in list_voices():
            print(name)
        return 0

    warnings: list[str] = []
    try:
        cfg, path = load_config(args, warnings)
    except config_mod.ConfigError as e:
        _show_error(f"Config error: {e}", gui=not (args.console or args.demo or args.test_voice))
        return 2

    if args.replay:
        return replay(args.replay, cfg, vref_set=args.vref is not None, mins_set=args.minimums is not None)

    terminal = args.console or args.demo or args.test_voice
    if not terminal:
        try:
            from landing_callouts import gui
        except ImportError as e:  # tkinter missing
            print(f"Can't open the window ({e}). Running in the terminal instead.")
        else:
            try:
                gui.run(cfg, path, use_sim=not args.no_sim, warnings=warnings)
            except Exception:  # noqa: BLE001 - with no console, a crash would otherwise vanish silently
                log = path.parent / "landing-callouts-error.log"
                log.write_text(traceback.format_exc(), encoding="utf-8")
                _show_error(f"{APP_NAME} hit an error and closed.\nDetails were saved to:\n{log}", gui=True)
                return 1
            return 0

    for w in warnings:
        print(f"Config warning: {w}", file=sys.stderr)
    print(f"Config: {path}")
    use_sim = not (args.demo or args.test_voice or args.no_sim)
    ctl = Controller(cfg, path, use_sim=use_sim, print_only=args.print_only)
    try:
        ctl.start()
        if args.test_voice:
            ctl.test_voice()
            ctl.speaker.wait_idle(20)
        elif args.demo:
            ctl.start_demo(speed=args.demo_speed)
            run_console(ctl, until_demo_done=True)
        else:
            run_console(ctl)
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        ctl.shutdown()
    return 0


def replay(path: str, cfg: config_mod.Config, vref_set: bool, mins_set: bool) -> int:
    """Feed a recording through the engine. Prints events, and any callout that differs from the recording.

    Uses the vref/minimums from the recording's latest "settings:" line unless --vref/--minimums are given.
    """
    import re
    from dataclasses import replace

    from landing_callouts import airports
    from landing_callouts.descent import DC6_RATE_FPM, DescentPlanner
    from landing_callouts.engine import CalloutEngine
    from landing_callouts.recorder import read_recording

    rows = read_recording(path)
    engine = CalloutEngine(cfg)
    planner = DescentPlanner(cfg)
    rate_override = None
    diffs = said = 0
    last_flaps = None
    for row in rows:
        event = row["event"]
        if event.startswith("settings:"):
            m = dict(re.findall(r"(\w+)=(\S+)", event))
            changes = {}
            if not vref_set and "vref" in m:
                changes["vref_kt"] = int(m["vref"])
            if not mins_set and "minimums" in m:
                changes["minimums_ft"] = int(m["minimums"])
            if "style" in m and m["style"] in config_mod.STYLES:
                changes["callout_style"] = m["style"]
            if "landing" in m:
                changes["landing_target_fpm"] = int(m["landing"])
            for key in ("v1", "vr", "v2"):
                if key in m:
                    changes[f"{key}_kt"] = int(m[key])
            if "speed" in m and m["speed"] in config_mod.SPEED_MODES:
                changes["speed_callouts"] = m["speed"]
            cfg = config_mod.with_overrides(cfg, **changes)
            engine = CalloutEngine(cfg)  # the live app also starts a fresh engine on a settings change
            engine.dc6 = m.get("dc6") == "True"
            planner.cfg = cfg
            planner.rate_fpm = rate_override or (DC6_RATE_FPM if engine.dc6 else None)
            planner.dc6 = engine.dc6
        if event.startswith("descent rate:"):
            rate_override = int(event.split(":", 1)[1])
            planner.rate_fpm = rate_override
        if event.startswith("destination:"):
            code = event.split(":", 1)[1].strip()
            planner.set_destination(None if code == "none" else airports.find(code))
        if event:
            print(f"{row['wall_time']}  -- {event}")
        s = row.get("sample")
        if s is None:
            continue
        # a blank flaps reading keeps the last one, as sim.py now does (older recordings have the blanks)
        if s.flaps_deg is None and last_flaps is not None:
            s = replace(s, flaps_deg=last_flaps)
        last_flaps = s.flaps_deg
        got = [c.text for c in engine.update(s) + planner.update(s)]
        said += len(got)
        flag = "" if got == row["callouts"] else "   <-- DIFFERENT"
        if got or row["callouts"]:
            diffs += bool(flag)
            print(f"{row['wall_time']}  AGL {s.agl_ft:6.0f}  IAS {s.ias_kt:3.0f}  VS {s.vs_fpm:+6.0f}  "
                  f"gear {'DN' if s.gear_down else 'UP'}  said {row['callouts']}  replay {got}{flag}")
    print(f"\n{said} callouts on replay, {diffs} rows differ from the recording.")
    return 0


def _show_error(msg: str, gui: bool) -> None:
    print(msg, file=sys.stderr)
    if gui:
        try:
            from tkinter import Tk, messagebox
            root = Tk()
            root.withdraw()
            messagebox.showerror(APP_TITLE, msg)
            root.destroy()
        except Exception:  # noqa: BLE001 - no GUI available; the message was printed
            pass


if __name__ == "__main__":
    sys.exit(main())
