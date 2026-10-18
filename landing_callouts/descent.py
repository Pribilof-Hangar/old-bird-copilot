"""Descent planning: when to leave cruise for the destination, and whether you're above the path.

The path: descend at the planned rate at the current groundspeed, to arrive at a height above the field a
set distance before it (room to slow down and configure). Universal mode uses the config (1500 ft, 5 nm);
DC-6 mode flies the classic DC-6 profile: 1000 fpm at cruise power down to a level-off 3000 ft above the
field, reached about 30 nm out, then slowing down level for the approach. Pure logic like the engine:
samples in, callouts out. The app keeps one planner for the whole session (the engine gets rebuilt on
every settings change; the descent state must survive that).
"""

from __future__ import annotations

from dataclasses import dataclass

from .airports import Airport, distance_nm
from .config import Config
from .engine import Callout, Sample

TOD_ARM_FT = 2000     # cruising this far above the arrival altitude ...
TOD_ARM_NM = 3        # ... and at least this far before the top of descent arms "top of descent"
HIGH_FT = 1000        # "high on profile" this far above the path
HIGH_RESET_FT = 300   # ... again only after getting back within this
DONE_FT = 500         # within this of the arrival altitude the descent is done: no more calls
DESCENDING_FPM = 300  # "high on profile" without a top-of-descent call: only while actually descending
MIN_GS_KT = 60        # below this (taxi, slow flight) there's no sensible path
DC6_RATE_FPM = 1000   # DC-6 profile: 1000 fpm at cruise power ...
DC6_LEVEL_AGL_FT = 3000   # ... to a level-off 3000 ft above the field ...
DC6_LEVEL_NM = 30         # ... about 30 nm out, then slow down level
LEVEL_OFF_WARN_FT = 500   # DC-6: "five hundred to level off" this far above the level-off altitude
LEVEL_OFF_REARM_FT = 1500
REQUEST_LEAD_MIN = 2.0    # "request descent" this long before the top of descent (time to call ATC)
TOD_EARLY_NM = 2.0        # starting down this close before the top of descent still gets "top of descent"
DESCENT_STEADY_S = 10.0   # "descending" = below -DESCENDING_FPM for this long
RATE_RANGE = (300, 2500)


def descent_distance(from_ft: float, to_ft: float, gs_kt: float, rate_fpm: float, margin_nm: float = 0
                     ) -> tuple[float, float]:
    """(nm before the point where you want to be at to_ft, minutes) for a steady descent."""
    minutes = max(0.0, from_ft - to_ft) / rate_fpm
    return minutes * gs_kt / 60 + margin_nm, minutes


@dataclass
class DescentInfo:
    """For the window: where you are relative to the plan."""
    dist_nm: float
    arrive_ft: int                 # arrival altitude above sea level (field + pattern height)
    tod_nm: float | None = None    # miles until the top of descent (0 or less: time to be descending)
    path_ft: float | None = None   # altitude the path wants you at now
    dev_ft: float | None = None    # you minus the path: positive = high
    descending: bool = False       # coming down now (an early start keeps the top of descent just ahead)


ON_PATH_FT = 300      # the window says "on path" within this


@dataclass
class Readouts:
    """The Descent panel's numbers, as text: a big value and a small line under it per column."""
    distance: str = "—"
    start_down: str = "—"
    start_sub: str = ""
    path: str = "—"          # where the path wants you now
    path_sub: str = ""
    diff: str = "—"          # you vs the path
    diff_sub: str = ""
    alert: bool = False      # time to start down, or high: shown in amber


def readouts(info: DescentInfo | None, alt_ft: float | None, gs_kt: float | None, margin_nm: float
             ) -> Readouts:
    r = Readouts()
    if info is None:
        return r
    r.distance = f"{info.dist_nm:,.0f} nm"
    if alt_ft is None:
        return r
    above = alt_ft - info.arrive_ft
    r.path_sub = f"you {round(alt_ft, -2):,.0f} ft"
    moving = gs_kt is not None and gs_kt >= MIN_GS_KT
    if moving and info.dist_nm > margin_nm and above > DONE_FT:
        minutes = (info.dist_nm - margin_nm) / gs_kt * 60
        r.diff_sub = f"needs {round(above / minutes, -1):,.0f} fpm"   # from here, to arrive on time
    if above <= DONE_FT:
        r.start_down, r.path, r.path_sub = "done", f"{info.arrive_ft:,} ft", "arrival altitude"
        return r
    if info.tod_nm is None:
        return r   # on the ground / too slow for a path
    if info.descending:
        # On the way down. On (or just below) the path the top of descent sits right here or just ahead,
        # so it would keep saying "NOW" or "in 1 nm": say "started" instead.
        r.start_down = "started"
        if info.tod_nm > 0.5:
            r.start_sub = f"{info.tod_nm:,.0f} nm early"
    elif info.tod_nm > 0.5:
        r.start_down = f"in {info.tod_nm:,.0f} nm"
        r.start_sub = f"about {info.tod_nm / gs_kt * 60:,.0f} min"
        r.path = "cruise"
        r.path_sub = f"you {round(alt_ft, -2):,.0f} ft"
        return r
    elif info.tod_nm > -0.5:
        r.start_down, r.alert = "NOW", True
    else:
        r.start_down, r.start_sub = "passed", f"{-info.tod_nm:,.0f} nm ago"
    r.path = f"{round(info.path_ft, -2):,.0f} ft"
    if abs(info.dev_ft) < ON_PATH_FT:
        r.diff = "on path"
    elif info.dev_ft > 0:
        r.diff, r.alert = f"{round(info.dev_ft, -2):,.0f} high", True
    else:
        r.diff = f"{round(-info.dev_ft, -2):,.0f} low"   # below the path while descending: ease off a little
    return r


class DescentPlanner:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.destination: Airport | None = None
        self.info: DescentInfo | None = None
        self.rate_fpm: int | None = None   # set by the app (calculator slider / DC-6 mode); else the config's
        self.dc6 = False                   # DC-6 mode (set by the app): the DC-6 profile and level-off call
        self._level_armed = False
        self._down_since: float | None = None
        self._reset()

    @property
    def arrive_agl_ft(self) -> int:
        """Height above the field the descent ends at (DC-6: the level-off altitude)."""
        return DC6_LEVEL_AGL_FT if self.dc6 else self.cfg.descent_pattern_ft

    @property
    def margin_nm(self) -> int:
        """How far out to be at that height."""
        return DC6_LEVEL_NM if self.dc6 else self.cfg.descent_margin_nm

    def _reset(self) -> None:
        self._tod_armed = False     # cruising before the top of descent
        self._tod_called = False    # "top of descent" said on this leg
        self._request_called = False   # "request descent" said on this leg
        self._high_armed = True

    def set_destination(self, airport: Airport | None) -> None:
        self.destination = airport
        self.info = None
        self._reset()

    def update(self, s: Sample) -> list[Callout]:
        ap, cfg = self.destination, self.cfg
        alt = s.altitude_ft   # the altimeter's reading, like the crew plans with
        if ap is None or s.lat is None or s.lon is None or alt is None:
            self.info = None
            return []
        margin = self.margin_nm
        info = DescentInfo(distance_nm(s.lat, s.lon, ap.lat, ap.lon), ap.elev_ft + self.arrive_agl_ft)
        above = alt - info.arrive_ft
        if s.gs_kt is not None and s.gs_kt >= MIN_GS_KT:
            nm_per_ft = s.gs_kt / 60 / (self.rate_fpm or cfg.descent_rate_fpm)
            info.tod_nm = info.dist_nm - max(0.0, above) * nm_per_ft - margin
            info.path_ft = info.arrive_ft + max(0.0, info.dist_nm - margin) / nm_per_ft
            info.dev_ft = alt - info.path_ft
        # steadily descending (a turbulence dip in cruise doesn't count)
        if s.vs_fpm < -DESCENDING_FPM:
            if self._down_since is None:
                self._down_since = s.t
        else:
            self._down_since = None
        info.descending = self._down_since is not None and s.t - self._down_since >= DESCENT_STEADY_S
        self.info = info

        if s.on_ground:
            self._reset()
            self._level_armed = False
            return []
        if not cfg.descent_calls or info.tod_nm is None:
            return []
        out = []
        if above > TOD_ARM_FT and info.tod_nm > TOD_ARM_NM:
            self._tod_armed = True
        # A heads-up to ask ATC for lower, so the clearance comes before the top of descent
        if (cfg.request_descent_call and self._tod_armed and not self._request_called
                and 0 < info.tod_nm <= s.gs_kt / 60 * REQUEST_LEAD_MIN):
            self._request_called = True
            out.append(Callout("request_descent", cfg.text("request_descent", "request descent"), 10))
        # Due, or started down a little early (on a path the top of descent then stays just ahead). Started
        # much earlier (an early ATC clearance): no call, the descent has simply begun.
        if self._tod_armed and info.descending and info.tod_nm > TOD_EARLY_NM:
            self._tod_armed = False
            self._tod_called = True
        if self._tod_armed and (info.tod_nm <= 0 or (info.descending and info.tod_nm <= TOD_EARLY_NM)):
            self._tod_armed = False
            self._tod_called = True
            out.append(Callout("top_of_descent", cfg.text("top_of_descent", "top of descent"), 10))
        # High: past the top of descent, still well above the arrival altitude, and not while climbing out
        # on a short hop (only after "top of descent" on this leg, or while actually descending).
        if (info.tod_nm <= 0 and above > DONE_FT and info.dist_nm > margin
                and (self._tod_called or s.vs_fpm < -DESCENDING_FPM)
                and self._high_armed and info.dev_ft >= HIGH_FT):
            self._high_armed = False
            out.append(Callout("high_on_profile", cfg.text("high_on_profile", "high on profile"), 10))
        if info.dev_ft < HIGH_RESET_FT:
            self._high_armed = True
        # DC-6: "five hundred to level off" descending through 500 ft above the level-off altitude
        if above > LEVEL_OFF_REARM_FT:
            self._level_armed = True
        if (self.dc6 and self._level_armed and above <= LEVEL_OFF_WARN_FT
                and s.vs_fpm < -DESCENDING_FPM):
            self._level_armed = False
            out.append(Callout("level_off", cfg.text("level_off", "five hundred to level off"), 6))
        return out
