"""App core shared by the GUI and the console mode: owns the config, the voice and the sim thread.

No tkinter here. The UI reads `status`, `latest` and `armed`, and drains `events`.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from pathlib import Path

from . import airports
from . import config as config_mod
from . import vref as vref_mod
from .config import Config
from .demo import run_demo
from .descent import DC6_RATE_FPM, DescentPlanner
from .engine import Callout, CalloutEngine, Sample, Touchdown
from .numbers import number_words
from .recorder import FlightRecorder
from .speech import PrintSpeaker, make_speaker, remove_stale_temp_dirs

RECONNECT_S = 5
TICK_S = 0.1

# Controller.status values
WAITING, CONNECTED, PAUSED, NO_SIM = "waiting", "connected", "paused", "no_sim"


def countdown_sample(cfg: Config) -> list[Callout]:
    """The last part of the countdown the way this config says it: "fifty, forty, thirty, twenty, ten/retard"."""
    out = [Callout(str(h), cfg.text(str(h), number_words(h)), 10) for h in (50, 40, 30, 20)]
    if cfg.retard_ft > 0:
        out.append(Callout("retard", cfg.text("retard"), 10))
    else:
        out.append(Callout("10", cfg.text("10", number_words(10)), 10))
    return out


class Controller:
    def __init__(self, cfg: Config, config_path: Path, use_sim: bool = True, print_only: bool = False):
        self.cfg = cfg
        self.config_path = Path(config_path)
        self.use_sim = use_sim
        self.print_only = print_only
        self.flight_vref = cfg.vref_kt
        self.flight_minimums = cfg.minimums_ft
        self.flight_landing_target = cfg.landing_target_fpm
        self.flight_v1, self.flight_vr, self.flight_v2 = cfg.v1_kt, cfg.vr_kt, cfg.v2_kt
        self.dc6_override: bool | None = None      # DC-6 mode: None = automatic (from the aircraft's name)
        self.muted = False

        self.status = WAITING if use_sim else NO_SIM
        self.demo_running = False
        self.latest: Sample | None = None
        self.armed: list[int] = []
        self.speed_watch = False    # the engine is judging speed against Vref right now (approach)
        self.aircraft_title: str | None = None
        self.suggestion: vref_mod.Suggestion | None = None   # suggested Vref from live weight (DC-6 table)
        self.takeoff_suggestion: vref_mod.TakeoffSpeeds | None = None
        # ("log", str) | ("callout", str) | ("touchdown", Touchdown) | ("status", str) | ("demo_done", None)
        self.events: queue.Queue = queue.Queue()

        self.speaker = None
        self._stop = threading.Event()
        self._demo_stop = threading.Event()
        self._engine_dirty = True
        self._throttle_dirty = False
        self._sim_thread: threading.Thread | None = None
        self.recorder = FlightRecorder(cfg.base_dir / "flights", log=self.log)
        # Descent planning to the destination typed in the window. Kept for the whole session (the engine is
        # rebuilt on every settings change).
        self.descent = DescentPlanner(cfg)
        self.descent_rate_override: int | None = None   # moved with the calculator slider (this session)

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self.use_sim:              # the real app (tests and demos leave TEMP alone)
            remove_stale_temp_dirs()
        self.speaker = self._make_speaker(self.cfg)
        if self.use_sim:
            self._sim_thread = threading.Thread(target=self._sim_loop, name="sim", daemon=True)
            self._sim_thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self._demo_stop.set()
        if self._sim_thread is not None:
            self._sim_thread.join(timeout=3)
        self.recorder.stop()
        if self.speaker is not None:
            self.speaker.stop()

    # ------------------------------------------------------------- settings

    def flight_config(self) -> Config:
        return config_mod.with_overrides(self.cfg, vref_kt=self.flight_vref, minimums_ft=self.flight_minimums,
                                         landing_target_fpm=self.flight_landing_target,
                                         v1_kt=self.flight_v1, vr_kt=self.flight_vr, v2_kt=self.flight_v2)

    @property
    def dc6_detected(self) -> bool:
        return vref_mod.is_dc6(self.aircraft_title)

    @property
    def dc6_active(self) -> bool:
        """DC-6 mode: automatic from the aircraft's name, unless switched on/off for this session."""
        return self.dc6_override if self.dc6_override is not None else self.dc6_detected

    def set_dc6_mode(self, on: bool) -> None:
        self.dc6_override = None if on == self.dc6_detected else on
        self.log("DC-6 mode " + ("on" if on else "off") + (" (automatic)" if self.dc6_override is None
                                                           else " (until the app is closed)"))

    def set_takeoff(self, v1: int, vr: int, v2: int) -> None:
        if (v1, vr, v2) != (self.flight_v1, self.flight_vr, self.flight_v2):
            self.flight_v1, self.flight_vr, self.flight_v2 = v1, vr, v2
            self._engine_dirty = True
            self.recorder.event(self._settings_note())

    def set_flight(self, vref_kt: int, minimums_ft: int, landing_target_fpm: int | None = None) -> None:
        target = self.flight_landing_target if landing_target_fpm is None else landing_target_fpm
        if (vref_kt, minimums_ft, target) != (self.flight_vref, self.flight_minimums, self.flight_landing_target):
            self.flight_vref, self.flight_minimums, self.flight_landing_target = vref_kt, minimums_ft, target
            self._engine_dirty = True
            self.recorder.event(self._settings_note())

    def _settings_note(self) -> str:
        c = self.flight_config()
        return (f"settings: style={c.callout_style} vref={c.vref_kt} minimums={c.minimums_ft} "
                f"landing={c.landing_target_fpm} v1={c.v1_kt} vr={c.vr_kt} v2={c.v2_kt} dc6={self.dc6_active} "
                f"speed={c.speed_callouts} retard={c.retard_ft} heights={c.heights}")

    def set_destination(self, code: str) -> airports.Airport | None:
        """Look up the destination for descent planning ("" clears it). Returns the airport, or None."""
        code = code.strip().upper()
        current = self.descent.destination
        if current is not None and current.code == code:
            return current
        airport = airports.find(code) if code else None
        if current is None and airport is None:
            return None
        self.descent.set_destination(airport)
        if airport is not None:
            self.log(f"Destination {airport.code}: {airport.name}, elevation {airport.elev_ft:,} ft.")
            self.recorder.event(f"destination: {airport.code}")
        else:
            self.recorder.event("destination: none")
        return airport

    @property
    def recommended_descent_rate(self) -> int:
        return DC6_RATE_FPM if self.dc6_active else self.cfg.descent_rate_fpm

    @property
    def descent_rate(self) -> int:
        """The planned descent rate: the calculator slider, or the recommended one (DC-6 mode: 1000 fpm)."""
        return self.descent_rate_override or self.recommended_descent_rate

    def sync_descent(self) -> None:
        """Give the planner the current rate and mode (also before the sim connects, for planning)."""
        self.descent.rate_fpm = self.descent_rate
        self.descent.dc6 = self.dc6_active

    def set_descent_rate(self, fpm: int | None) -> None:
        """From the calculator slider (None = back to the recommended rate)."""
        override = None if fpm == self.recommended_descent_rate else fpm
        if override != self.descent_rate_override:
            self.descent_rate_override = override
            self.recorder.event(f"descent rate: {self.descent_rate}")

    def set_height_callouts(self, on: bool) -> None:
        """Main-window switch (saved to config.toml)."""
        if on != self.cfg.height_callouts:
            self.apply_settings(config_mod.with_overrides(self.cfg, height_callouts=on))
            self.log("Height callouts on." if on else
                     "Height callouts off (speed, flaps, warnings and landing calls stay on).")

    def flag_moment(self) -> None:
        """Drop a marker in the recording ("something sounded wrong here")."""
        if self.recorder.active:
            self.recorder.event("FLAG")
            self.log(f"Flagged {time.strftime('%H:%M:%S')} in flights\\{self.recorder.path.name}")
        else:
            self.log("Nothing to flag: recording only runs while connected to MSFS (and if on in Settings).")

    def apply_settings(self, new: Config, save: bool = True) -> None:
        """Use new settings right away (and write them to config.toml)."""
        if save:
            config_mod.save(new, self.config_path)
        old, self.cfg = self.cfg, new
        self.descent.cfg = new
        voice_keys = ("voice", "rate", "volume", "sounds_dir")
        if any(getattr(old, k) != getattr(new, k) for k in voice_keys):
            old_speaker, self.speaker = self.speaker, self._make_speaker(new)
            if old_speaker is not None:
                old_speaker.stop()
        if old.throttle_simvar != new.throttle_simvar:
            self._throttle_dirty = True
        self._engine_dirty = True
        self.recorder.event(self._settings_note())

    def _make_speaker(self, cfg: Config):
        if self.print_only:
            return PrintSpeaker(log=lambda _msg: None)  # callouts already appear as "callout" events
        return make_speaker(cfg, log=self.log)

    # --------------------------------------------------------------- speech

    def log(self, msg: str) -> None:
        self.events.put(("log", msg))

    def speak(self, c: Callout) -> None:
        self.events.put(("callout", c.text))
        if not self.muted:
            self.speaker.say(c)

    def test_voice(self) -> None:
        for c in countdown_sample(self.cfg):
            self.speaker.say(c)

    # ----------------------------------------------------------------- demo

    def start_demo(self, speed: float = 1.0) -> None:
        if self.demo_running:
            return
        self.demo_running = True
        self._demo_stop.clear()
        threading.Thread(target=self._demo_thread, args=(speed,), name="demo", daemon=True).start()

    def stop_demo(self) -> None:
        self._demo_stop.set()

    def _demo_thread(self, speed: float) -> None:
        self.log("Demo approach started.")
        engine = CalloutEngine(self.flight_config())
        try:
            finished = run_demo(lambda s: self._process(engine, s, record=False), speed=speed, stop=self._demo_stop)
            self.log("Demo approach finished." if finished else "Demo approach stopped.")
        finally:
            self.demo_running = False
            self.latest = None
            self.events.put(("demo_done", None))

    # ------------------------------------------------------------------ sim

    def _process(self, engine: CalloutEngine, s: Sample, record: bool = True) -> None:
        before = engine.last_touchdown
        engine.dc6 = self.dc6_active
        table = "DC-6" if self.dc6_active else None          # the DC-6 tables follow DC-6 mode
        if s.weight_lb is not None or table is None:          # a missed weight reading keeps the last one
            self.suggestion = vref_mod.suggest(table, s.weight_lb)
        self.sync_descent()
        callouts = engine.update(s) + self.descent.update(s)
        for c in callouts:
            self.speak(c)
        if record:
            self.recorder.sample(s, [c.text for c in callouts])
        self.latest = s
        self.armed = engine.armed_heights()
        self.speed_watch = engine.speed_watch
        headwind = None
        if s.wind_kt is not None and s.wind_dir_deg is not None and s.heading_deg is not None:
            headwind = s.wind_kt * math.cos(math.radians(s.wind_dir_deg - s.heading_deg))
        # Take-off speeds only matter before take-off: shown on the ground, hidden once airborne
        # (otherwise they keep changing with fuel burn all the way to cruise).
        self.takeoff_suggestion = (vref_mod.takeoff(table, s.weight_lb, s.ground_elev_ft, headwind)
                                   if s.on_ground else None)
        for line in engine.events:
            if line.startswith("Bounce"):
                self.log(line)
                if record:
                    self.recorder.event(line)
        if engine.last_touchdown is not before:
            td: Touchdown = engine.last_touchdown
            self.log(str(td))
            self.events.put(("touchdown", td))
            if record:
                self.recorder.event(str(td))

    def _set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self.events.put(("status", status))
            self.recorder.event(f"status: {status}")

    def _sim_loop(self) -> None:
        from .sim import SimReader

        reader = SimReader(self.cfg)
        self.log("Waiting for MSFS...")
        try:
            while not self._stop.is_set():
                self._set_status(WAITING)
                reader.cfg = self.cfg
                try:
                    reader.connect()
                except ConnectionError:
                    self._stop.wait(RECONNECT_S)
                    continue
                except Exception as e:  # noqa: BLE001 - e.g. SimConnect.dll failed to load
                    self.log(f"SimConnect error: {e}")
                    self._stop.wait(RECONNECT_S)
                    continue

                self.log("Connected to MSFS.")
                self._set_status(CONNECTED)
                try:
                    self._run_connected(reader)
                except Exception as e:  # noqa: BLE001 - keep callouts alive: log, reconnect
                    self.log(f"Error while reading the sim ({type(e).__name__}: {e}). Reconnecting...")
                    self.recorder.event(f"error: {type(e).__name__}: {e}")
                self.recorder.event("disconnected")
                self.recorder.stop()
                reader.close()
                self.latest = None
                self.suggestion = self.takeoff_suggestion = None
                self.aircraft_title = None
                if not self._stop.is_set():
                    self.log("Disconnected from MSFS. Waiting for it again...")
        finally:
            reader.close()

    def _run_connected(self, reader) -> None:
        """Read the sim about 10 times a second until it closes or the app stops."""
        self._throttle_dirty = False
        engine: CalloutEngine | None = None
        while not self._stop.is_set() and reader.connected:
            start = time.monotonic()
            reader.cfg = self.cfg
            if self.cfg.record_flights and not self.recorder.active:
                self.recorder.start()
                self.recorder.event(self._settings_note())
                try:
                    info = reader.aircraft_info()
                    self.recorder.event("aircraft: " + " ".join(f"{k}={v}" for k, v in info.items()))
                except Exception:  # noqa: BLE001 - nice-to-have only
                    pass
            elif not self.cfg.record_flights and self.recorder.active:
                self.recorder.stop()
            if self._throttle_dirty:
                self._throttle_dirty = False
                reader.reset_throttle()
            if reader.paused:
                self._set_status(PAUSED)
            else:
                self._set_status(CONNECTED)
                s = reader.read()
                if reader.title != self.aircraft_title and reader.title:
                    self.aircraft_title = reader.title
                    extra = " (DC-6 landing speed table on)" if vref_mod.is_dc6(reader.title) else ""
                    self.log(f"Aircraft: {reader.title}{extra}")
                    self.recorder.event(f"aircraft title: {reader.title}")
                if s is not None and not self.demo_running:
                    if engine is None or self._engine_dirty:
                        self._engine_dirty = False
                        engine = CalloutEngine(self.flight_config())
                    self._process(engine, s)
            self._stop.wait(max(0.0, TICK_S - (time.monotonic() - start)))
