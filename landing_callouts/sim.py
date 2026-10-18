"""Read the sim through SimConnect and turn it into engine Samples."""

from __future__ import annotations

import time

from .config import Config
from .engine import Sample

# name -> (SimVar, units)
SIMVARS = {
    "agl_ft": ("PLANE ALT ABOVE GROUND MINUS CG", "Feet"),
    "ias_kt": ("AIRSPEED INDICATED", "Knots"),
    "vs_fpm": ("VERTICAL SPEED", "Feet per minute"),
    "on_ground": ("SIM ON GROUND", "Bool"),
    "gear_down": ("GEAR HANDLE POSITION", "Bool"),
}


OVERSPEED_EVERY = 3   # read the overspeed flags every Nth reading (~3 times a second)
WEIGHT_EVERY = 10     # read weight and fuel every Nth reading (~once a second; offset from the flags)
TITLE_EVERY = 50      # read the aircraft's name every Nth reading (~every 5 s; catches aircraft changes)


def _bool_or_none(v) -> bool | None:
    return None if v is None else bool(v)


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", errors="replace")
    v = str(v).strip("\x00 ").strip()
    return v or None


class SimReader:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sm = None
        self._requests = {}
        self._throttle = None
        self._flaps = None
        self._last_flaps = None
        self._flap_over = self._gear_over = self._touchdown = None
        self._touchdown_fps = None
        self._over_values = (None, None)
        self._weight = self._fuel = self._title = self._elev = None
        self._wind = []
        self._wind_values = (None, None, None)
        self._nav = []
        self._nav_values = (None, None, None, None, None)
        self._weights = (None, None, None)
        self.title: str | None = None        # aircraft title from the sim, e.g. "PMDG DC-6A ..."
        self._reads = 0

    def connect(self) -> None:
        """Raises ConnectionError if the sim isn't running."""
        from SimConnect import Request, SimConnect

        self.sm = SimConnect()
        self._requests = {
            name: Request((var.encode(), units.encode()), self.sm, _time=0, _attemps=10)
            for name, (var, units) in SIMVARS.items()
        }
        # Optional: a missing value doesn't skip the tick.
        self._flaps = Request((b"TRAILING EDGE FLAPS LEFT ANGLE", b"Degrees"), self.sm, _time=0, _attemps=5)
        # The sim's own overspeed flags (from the aircraft's limits). Slow-changing, so read every few ticks.
        self._flap_over = Request((b"FLAP SPEED EXCEEDED", b"Bool"), self.sm, _time=0, _attemps=5)
        self._gear_over = Request((b"GEAR SPEED EXCEEDED", b"Bool"), self.sm, _time=0, _attemps=5)
        self._touchdown = Request((b"PLANE TOUCHDOWN NORMAL VELOCITY", b"Feet per second"), self.sm, _time=0,
                                  _attemps=5)
        self._weight = Request((b"TOTAL WEIGHT", b"Pounds"), self.sm, _time=0, _attemps=5)
        self._fuel = Request((b"FUEL TOTAL QUANTITY WEIGHT", b"Pounds"), self.sm, _time=0, _attemps=5)
        self._elev = Request((b"GROUND ALTITUDE", b"Feet"), self.sm, _time=0, _attemps=5)
        self._title = Request((b"TITLE", b"String"), self.sm, _time=0, _attemps=5)
        self._wind = [Request((var, units), self.sm, _time=0, _attemps=5) for var, units in (
            (b"AMBIENT WIND DIRECTION", b"Degrees"), (b"AMBIENT WIND VELOCITY", b"Knots"),
            (b"PLANE HEADING DEGREES TRUE", b"Degrees"))]
        self._nav = [Request((var, units), self.sm, _time=0, _attemps=5) for var, units in (
            (b"PLANE LATITUDE", b"Degrees"), (b"PLANE LONGITUDE", b"Degrees"),
            (b"GROUND VELOCITY", b"Knots"), (b"PLANE ALTITUDE", b"Feet"), (b"INDICATED ALTITUDE", b"Feet"))]
        self._nav_values = (None, None, None, None, None)
        self._wind_values = (None, None, None)
        self.title = None
        self._over_values = (None, None)
        self._weights = (None, None, None)
        self._touchdown_fps = None
        self._last_flaps = None
        self._reads = 0
        self.reset_throttle()

    def aircraft_info(self) -> dict:
        """Fixed numbers for the loaded aircraft (for the recording; a future suggested-Vref estimate)."""
        from SimConnect import Request

        info = {"title": _text(Request((b"TITLE", b"String"), self.sm, _time=0, _attemps=10).value)}
        for key, var, units in (("max_gross_lb", b"MAX GROSS WEIGHT", b"Pounds"),
                                ("empty_lb", b"EMPTY WEIGHT", b"Pounds"),
                                ("vs0_kt", b"DESIGN SPEED VS0", b"Knots")):
            v = Request((var, units), self.sm, _time=0, _attemps=10).value
            info[key] = None if v is None else round(float(v), 1)
        return info

    def reset_throttle(self) -> None:
        """(Re)create the throttle request from cfg.throttle_simvar. Call from the reading thread."""
        from SimConnect import Request

        self._throttle = None
        if self.sm is not None and self.cfg.throttle_simvar:
            self._throttle = Request((self.cfg.throttle_simvar.encode(), b"Percent"), self.sm, _time=0, _attemps=5)

    @property
    def connected(self) -> bool:
        return self.sm is not None and self.sm.quit != 1

    @property
    def paused(self) -> bool:
        return bool(self.sm is not None and self.sm.paused)

    def read(self) -> Sample | None:
        """One fresh Sample, or None if any required value didn't arrive in time."""
        values = {}
        for name, req in self._requests.items():
            v = req.value
            if v is None:
                return None
            values[name] = v
        throttle = self._throttle.value if self._throttle is not None else None
        flaps = self._flaps.value if self._flaps is not None else None
        # a missed flaps reading keeps the last one (a blank once caused a false "plus ten" on short final)
        if flaps is None:
            flaps = self._last_flaps
        self._last_flaps = flaps
        if self._reads % OVERSPEED_EVERY == 0:
            self._over_values = tuple(_bool_or_none(r.value) for r in (self._flap_over, self._gear_over))
            self._wind_values = tuple(None if (v := r.value) is None else float(v) for r in self._wind)
            td = self._touchdown.value
            self._touchdown_fps = float(td) if td is not None else None
        if self._reads % WEIGHT_EVERY == 1:
            # a missed reading keeps the last known value (so the suggested Vref doesn't blink)
            fresh = [r.value for r in (self._weight, self._fuel, self._elev)]
            self._weights = tuple(old if v is None else float(v) for v, old in zip(fresh, self._weights))
            # position for descent planning: once a second is plenty
            fresh = [r.value for r in self._nav]
            self._nav_values = tuple(old if v is None else float(v) for v, old in zip(fresh, self._nav_values))
        if self._reads % TITLE_EVERY == 2:
            self.title = _text(self._title.value) or self.title
        self._reads += 1
        flap_over, gear_over = self._over_values
        weight, fuel, elev = self._weights
        return Sample(
            t=time.monotonic(),
            agl_ft=float(values["agl_ft"]) + self.cfg.agl_offset_ft,
            ias_kt=float(values["ias_kt"]),
            vs_fpm=float(values["vs_fpm"]),
            on_ground=bool(values["on_ground"]),
            gear_down=bool(values["gear_down"]),
            throttle_pct=float(throttle) if throttle is not None else None,
            flaps_deg=float(flaps) if flaps is not None else None,
            flap_overspeed=flap_over,
            gear_overspeed=gear_over,
            sim_touchdown_fps=self._touchdown_fps,
            weight_lb=weight,
            fuel_lb=fuel,
            wind_dir_deg=self._wind_values[0],
            wind_kt=self._wind_values[1],
            heading_deg=self._wind_values[2],
            ground_elev_ft=elev,
            lat=self._nav_values[0],
            lon=self._nav_values[1],
            gs_kt=self._nav_values[2],
            alt_msl_ft=self._nav_values[3],
            alt_ind_ft=self._nav_values[4],
        )

    def close(self) -> None:
        if self.sm is not None:
            try:
                self.sm.exit()
            except Exception:  # noqa: BLE001 - the sim may already be gone
                pass
            self.sm = None
