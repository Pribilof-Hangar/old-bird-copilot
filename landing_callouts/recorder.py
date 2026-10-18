"""Flight recorder: writes what the sim reported and what was said to a CSV per session.

Used to find bugs: a recording can be replayed through the engine (`--replay FILE`) to reproduce
exactly what happened on a flight. Recording must never break callouts, so every write is guarded.
"""

from __future__ import annotations

import csv
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .engine import Sample

COLUMNS = ["wall_time", "t", "agl_ft", "ias_kt", "vs_fpm", "on_ground", "gear_down", "throttle_pct",
           "flaps_deg", "flap_over", "gear_over", "sim_td_fps", "weight_lb", "fuel_lb", "wind_dir", "wind_kt", "hdg_true",
           "lat", "lon", "gs_kt", "alt_msl", "alt_ind", "callouts", "event"]
FILE_GLOB = "flight-*.csv"
KEEP = 20
FULL_RATE_BELOW_FT = 3000   # record every sample below this (approach, flare)
FULL_RATE_ABOVE_KT = 30     # ... and on the ground above this (takeoff and landing roll)
SLOW_INTERVAL_S = 1.0       # otherwise (cruise, taxi) once a second


class FlightRecorder:
    def __init__(self, folder: Path, keep: int = KEEP, log: Callable[[str], None] = print):
        self.folder = Path(folder)
        self.keep = keep
        self._log = log
        self._lock = threading.Lock()
        self._file = None
        self._writer = None
        self._last_t: float | None = None
        self.path: Path | None = None

    @property
    def active(self) -> bool:
        return self._file is not None

    def start(self) -> None:
        with self._lock:
            if self._file is not None:
                return
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                name = time.strftime("flight-%Y%m%d-%H%M%S.csv")
                self.path = self.folder / name
                self._file = open(self.path, "w", newline="", encoding="utf-8", buffering=1)
                self._writer = csv.writer(self._file)
                self._writer.writerow(COLUMNS)
                self._last_t = None
            except OSError as e:
                self._fail(e)
                return
        self._prune()
        self._log(f"Recording this session to flights\\{self.path.name}")

    def stop(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    pass
            self._file = self._writer = None

    def sample(self, s: Sample, callouts: list[str]) -> None:
        full_rate = ((not s.on_ground and s.agl_ft < FULL_RATE_BELOW_FT)
                     or (s.on_ground and s.ias_kt > FULL_RATE_ABOVE_KT) or callouts)
        if not full_rate and self._last_t is not None and s.t - self._last_t < SLOW_INTERVAL_S:
            return
        self._last_t = s.t
        thr = "" if s.throttle_pct is None else f"{s.throttle_pct:.1f}"
        flaps = "" if s.flaps_deg is None else f"{s.flaps_deg:.1f}"
        self._write([_wall(), f"{s.t:.3f}", f"{s.agl_ft:.1f}", f"{s.ias_kt:.1f}", f"{s.vs_fpm:.0f}",
                     int(s.on_ground), int(s.gear_down), thr, flaps, _flag(s.flap_overspeed),
                     _flag(s.gear_overspeed), "" if s.sim_touchdown_fps is None else f"{s.sim_touchdown_fps:.2f}",
                     _num(s.weight_lb), _num(s.fuel_lb), _num(s.wind_dir_deg), _num(s.wind_kt), _num(s.heading_deg),
                     "" if s.lat is None else f"{s.lat:.5f}", "" if s.lon is None else f"{s.lon:.5f}",
                     _num(s.gs_kt), _num(s.alt_msl_ft), _num(s.alt_ind_ft), " | ".join(callouts), ""])

    def event(self, text: str) -> None:
        self._write([_wall(), f"{time.monotonic():.3f}"] + [""] * (len(COLUMNS) - 3) + [text])

    def _write(self, row: list) -> None:
        with self._lock:
            if self._writer is None:
                return
            try:
                self._writer.writerow(row)
            except OSError as e:
                self._fail(e)

    def _fail(self, e: OSError) -> None:
        # Called with the lock held. Recording stops; callouts carry on.
        self._file = self._writer = None
        self._log(f"Flight recording stopped: {e}")

    def _prune(self) -> None:
        try:
            files = sorted(self.folder.glob(FILE_GLOB))
            for old in files[:-self.keep] if self.keep > 0 else []:
                old.unlink(missing_ok=True)
        except OSError:
            pass


def _num(v: float | None) -> str:
    return "" if v is None else f"{v:.0f}"


def _flag(b: bool | None):
    return "" if b is None else int(b)


def _wall() -> str:
    now = time.time()
    return time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now % 1 * 10)}"


def read_recording(path: str | Path) -> list[dict]:
    """Rows of a recording as dicts. Sample rows have 'sample' (a Sample); event rows have 'event'."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            row = {"wall_time": r["wall_time"], "callouts": [c for c in r["callouts"].split(" | ") if c],
                   "event": r["event"]}
            if r["agl_ft"]:
                row["sample"] = Sample(
                    t=float(r["t"]), agl_ft=float(r["agl_ft"]), ias_kt=float(r["ias_kt"]),
                    vs_fpm=float(r["vs_fpm"]), on_ground=r["on_ground"] == "1", gear_down=r["gear_down"] == "1",
                    throttle_pct=float(r["throttle_pct"]) if r["throttle_pct"] else None,
                    flaps_deg=float(r["flaps_deg"]) if r.get("flaps_deg") else None,  # older files: no column
                    flap_overspeed=r["flap_over"] == "1" if r.get("flap_over") else None,
                    gear_overspeed=r["gear_over"] == "1" if r.get("gear_over") else None,
                    weight_lb=float(r["weight_lb"]) if r.get("weight_lb") else None,
                    fuel_lb=float(r["fuel_lb"]) if r.get("fuel_lb") else None,
                    wind_dir_deg=float(r["wind_dir"]) if r.get("wind_dir") else None,
                    wind_kt=float(r["wind_kt"]) if r.get("wind_kt") else None,
                    heading_deg=float(r["hdg_true"]) if r.get("hdg_true") else None,
                    lat=float(r["lat"]) if r.get("lat") else None,
                    lon=float(r["lon"]) if r.get("lon") else None,
                    gs_kt=float(r["gs_kt"]) if r.get("gs_kt") else None,
                    alt_msl_ft=float(r["alt_msl"]) if r.get("alt_msl") else None,
                    alt_ind_ft=float(r["alt_ind"]) if r.get("alt_ind") else None)
            rows.append(row)
    return rows
