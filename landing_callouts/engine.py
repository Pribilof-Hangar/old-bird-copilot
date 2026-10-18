"""Pure callout logic: samples in, callouts out. No sim, no audio, no Windows imports."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config
from .numbers import digit_words, number_words

TELEPORT_FT = 500
GEAR_UP_OK_ABOVE_FT = 1000
GEAR_UP_MIN_DESCENT_FPM = 300
FLAP_SETTLE_S = 1.0        # flaps must stop moving this long before "flaps thirty" is said
FLAP_CHANGE_DEG = 4        # smaller changes than this aren't announced
FLAP_MOVING_DEG = 0.1      # change between readings that counts as moving (slow flaps: ~0.5 deg/reading)
FINAL_ENTRY_FPM = 400      # "on final" starts after descending faster than this...
FINAL_ENTRY_S = 3.0        # ...for this long (a bump in level flight doesn't count)
FINAL_DESCENT_FPM = 200    # and lasts while still descending faster than this...
FINAL_MEMORY_S = 15.0      # ...now or within this long (rides over brief level-offs)
CLIMBING_FPM = 300         # climbing faster than this ends "on final" (go-around)
TOO_LOW_GEAR_MAX_KT = 190  # like real ground-proximity systems: no "too low, gear" when this fast
WIND_CHECK_FT = 1000       # "wind two seven zero at fifteen, crosswind ten from the left"
STABLE_CHECK_FT = 500      # "five hundred, stable" / "five hundred, unstable, speed"
TAKEOFF_ARM_KT = 30        # take-off calls arm when this slow on the ground (or on touchdown)
TAKEOFF_CALLS_BELOW_FT = 400   # "rotate"/"vee two" may come just after lift-off, but not later
TAKEOFF_DONE_FT = 1500     # take-off phase over above this
TAKEOFF_FLAPS_CHECK_KT = 60   # well above taxi speed (a 40 kt taxi once triggered it)
BOUNCE_S = 10.0            # another touchdown within this long is a bounce, not a new landing
POSITIVE_RATE_FPM = 300    # "positive rate": climbing faster than this...
POSITIVE_RATE_FT = 30      # ...and this high, after lift-off
DC6_TAKEOFF_FLAPS = (15, 25)   # DC-6 take-off flaps 20 (PMDG/AFE); outside this range: "check flaps"
DC6_REVERSE_CUE_KT = 40    # DC-6 checklist: out of reverse at 40 kt
DC6_LANDING_FLAPS_MIN = 35   # DC-6 landing flaps are 40 or full (50); speed calls wait for them
# DC-6 landing check: flaps full and three green on short final. Passing 300 ft (the user's choice): a
# distance like "3 miles" was measured to the airport's center, which can be miles past the threshold.
LANDING_CHECK_FT = 300
LANDING_CHECK_REARM_FT = 1500     # re-armed on the ground or above this (go-around)
DC6_FULL_FLAPS_MIN = 45       # full flaps are 50
LANDING_CHECK_MIN_FPM = 300   # descending at least this fast
XWIND_REMARK_KT = 25       # crosswind at least this strong gets a remark from the crew
XWIND_LATE_MIN_FT = 200    # a late crosswind call (once lined up) only between these heights -
XWIND_LATE_MAX_FT = 1500   # never in the flare
XWIND_REMARKS = ["these are some crosswinds!", "putting her to the test today, captain",
                 "gonna have to crab this one in"]
_remark_count = 0          # rotates the remarks across approaches (module level: survives engine rebuilds)
HEADING_STEADY_S = 10.0   # crosswind is only worked out when the heading has been steady this long...
HEADING_STEADY_DEG = 3     # ...within this many degrees (mid-turn it would be wrong for the runway;
                           # a slow curving approach at 0.7 deg/s once passed a 5 s / 5 deg test)
WIND_CALM_KT = 3          # less than this is "calm" (or a crosswind/tailwind not worth mentioning)
STABLE_MIN_FLAPS_DEG = 15  # flaps less than this at 500 ft counts as not configured for landing
DEV_BAND_MEMORY_S = 30.0   # after leaving final this long, the next approach announces its band fresh
DEV_HYSTERESIS_KT = 3      # speed must come this far back inside a band before it's left
DEV_MIN_GAP_S = 2.0        # at least this long between "plus ten" style calls
DEV_IMPROVE_HOLD_S = 5.0   # an improvement (+20 -> +15) is only called once it has held this long
EPS_S = 0.002             # timer slack, so replays of recordings (ms-rounded times) match exactly

# Winner when two callouts land on the same height (higher wins).
_PRIORITY = {"minimums": 4, "retard": 3, "approaching_minimums": 2}


@dataclass
class Sample:
    t: float              # time.monotonic() seconds
    agl_ft: float
    ias_kt: float
    vs_fpm: float
    on_ground: bool
    gear_down: bool
    throttle_pct: float | None = None
    flaps_deg: float | None = None     # trailing-edge flap angle
    flap_overspeed: bool | None = None # the sim's own "flap speed exceeded" flag (aircraft's limits)
    gear_overspeed: bool | None = None # the sim's own "gear speed exceeded" flag
    sim_touchdown_fps: float | None = None  # the sim's own last touchdown rate (recorded to compare)
    weight_lb: float | None = None     # total aircraft weight (fuel, freight, passengers included)
    fuel_lb: float | None = None       # fuel on board
    wind_dir_deg: float | None = None  # wind direction (from), degrees true
    wind_kt: float | None = None       # wind speed
    heading_deg: float | None = None   # aircraft heading, degrees true
    ground_elev_ft: float | None = None   # elevation of the ground below (airport elevation on the runway)
    lat: float | None = None              # position, for descent planning
    lon: float | None = None
    gs_kt: float | None = None            # groundspeed
    alt_msl_ft: float | None = None       # true altitude above sea level
    alt_ind_ft: float | None = None       # indicated altitude: what the altimeter shows with its pressure setting

    @property
    def altitude_ft(self) -> float | None:
        """The altitude to plan with: the altimeter's reading, like the crew (true altitude if not available)."""
        return self.alt_ind_ft if self.alt_ind_ft is not None else self.alt_msl_ft


@dataclass
class Callout:
    key: str              # "1000", "50", "retard", "minimums", "sink_rate", ...
    text: str             # what to say
    max_age_s: float      # drop it if it can't be spoken within this long


@dataclass
class Touchdown:
    vs_fpm: float
    ias_kt: float
    target_fpm: int = 0      # the flight's max touchdown rate; 0 = none set

    @property
    def within_target(self) -> bool | None:
        return None if not self.target_fpm else abs(self.vs_fpm) <= self.target_fpm

    def __str__(self) -> str:
        text = f"Touchdown: {self.vs_fpm:+.0f} fpm, {self.ias_kt:.0f} kt"
        if self.target_fpm:
            text += f" ({'within' if self.within_target else 'OVER'} {self.target_fpm} fpm target)"
        return text


def _beyond(dev: float, steps: list[int]) -> list[int]:
    """Bands past the top step, in tens: 51 kt over with steps up to 20 -> [30, 40, 50]."""
    top = max(steps or [0])
    return list(range(top + 10 - top % 10, int(dev // 10) * 10 + 1, 10)) if steps and dev >= top + 10 else []


def status_line(s: Sample, armed_heights: list[int]) -> str:
    armed = " ".join(str(h) for h in armed_heights) or "-"
    thr = f"{s.throttle_pct:.0f}%" if s.throttle_pct is not None else "n/a"
    flaps = f"{s.flaps_deg:.0f}" if s.flaps_deg is not None else "n/a"
    where = "GROUND" if s.on_ground else "air"
    return (f"AGL {s.agl_ft:5.0f} ft | IAS {s.ias_kt:3.0f} kt | VS {s.vs_fpm:+5.0f} fpm | "
            f"gear {'DOWN' if s.gear_down else 'UP'} | flaps {flaps} | thr {thr} | {where} | armed: {armed}")


def arm_margin(h: float) -> float:
    """How far above a height the plane must be before that height can be called again.
    20% (at least 10 ft), capped at 50 ft so a circuit flown at ~1050 ft still gets "one thousand"."""
    return min(50.0, max(10.0, 0.2 * h))


def build_thresholds(cfg: Config) -> dict[int, str]:
    """Merge heights, minimums and retard into one {height: key} table."""
    table: dict[int, str] = {}

    def put(h: int, key: str):
        if h <= 0:
            return
        old = table.get(h)
        if old is None or _PRIORITY.get(key, 1) > _PRIORITY.get(old, 1):
            table[h] = key

    if cfg.height_callouts:
        for h in cfg.heights:
            put(h, str(h))
    if cfg.minimums_ft > 0:
        put(cfg.minimums_ft, "minimums")
        if cfg.approaching_minimums:
            put(cfg.minimums_ft + 100, "approaching_minimums")
    if cfg.retard_ft > 0 and cfg.height_callouts:
        put(cfg.retard_ft, "retard")
    return table


class CalloutEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.thresholds = build_thresholds(cfg)
        self.armed: dict[int, bool] = {h: False for h in self.thresholds}
        self.prev: Sample | None = None
        self.retard_active = False
        self.last_retard_t = 0.0
        self.last_condition_t: dict[str, float] = {}
        self.landed = False             # on the ground after a touchdown (enables rollout callouts)
        self.rollout_done: set[int] = set()
        self.last_touchdown: Touchdown | None = None
        self.events: list[str] = []     # log lines from the latest update()
        self.flaps_reported: float | None = None   # last flap angle announced (or the starting angle)
        self.flaps_moved_t = 0.0                   # when the flaps last moved
        self.dev_band = 0                          # speed band last announced (+10, -5, ...; 0 = on speed)
        self.last_dev_t = float("-inf")
        self.on_final = False                      # descending toward a landing (see _track_final)
        self.descent_since: float | None = None    # when the current real descent started
        self.last_descent_t = float("-inf")       # last time we were descending like on final
        self.off_final_t: float | None = None      # when speed stopped being judged (for band memory)
        self.gate_armed = {"wind": False, "stable": False}   # 1000 ft wind / 500 ft stable checks
        self.wind_done = False                     # wind already called on this approach
        self.was_on_final = False
        self.heading_hist: list[tuple[float, float]] = []   # (t, heading) for the last few seconds
        self.dc6 = False                           # DC-6 mode (set by the app): flaps check, 40 kt reverse cue
        self.xwind_remarked = False                # crosswind remark already made on this approach
        self.landing_checked = False               # DC-6 landing check done on this approach
        self.to_ready = False                      # take-off phase (armed on the ground)
        self.to_said: set[str] = set()
        self.to_lifted = False
        self.to_flaps_checked = False
        self.to_from_slow = False                  # take-off roll started from slow (not a landing roll)
        self.last_touchdown_t: float | None = None
        self.speed_watch = False                   # speed is currently judged against Vref (for the UI too)
        self.dev_pending: int | None = None        # an improved band waiting to hold DEV_IMPROVE_HOLD_S
        self.dev_pending_t = 0.0

    # ------------------------------------------------------------------ public

    def armed_heights(self) -> list[int]:
        return sorted((h for h, a in self.armed.items() if a), reverse=True)

    def update(self, s: Sample) -> list[Callout]:
        self.events = []
        prev, self.prev = self.prev, s
        if prev is None:
            self._arm(s)
            self.flaps_reported = s.flaps_deg
            return []

        if abs(s.agl_ft - prev.agl_ft) > TELEPORT_FT:
            self._teleport(s)
            return []

        heights: list[Callout] = []
        retard: list[Callout] = []
        flaps: list[Callout] = self._flaps(prev, s)
        conditions: list[Callout] = []
        after: list[Callout] = []

        # Touchdown (a second contact within BOUNCE_S of the first is a bounce, not a new landing)
        if (s.on_ground and not prev.on_ground and self.last_touchdown_t is not None
                and s.t - self.last_touchdown_t < BOUNCE_S - EPS_S):
            self.events.append(f"Bounce: back on the ground {s.t - self.last_touchdown_t:.1f} s after touchdown "
                               f"({prev.vs_fpm:+.0f} fpm). The first touchdown counts.")
            self.landed = True
        elif s.on_ground and not prev.on_ground:
            self.last_touchdown_t = s.t
            td = Touchdown(prev.vs_fpm, prev.ias_kt, self.cfg.landing_target_fpm)
            self.last_touchdown = td
            self.events.append(str(td))
            self.retard_active = False
            self.landed = True
            self.rollout_done = set()
            rate = digit_words(round(abs(td.vs_fpm)))
            if td.target_fpm and self.cfg.speak_landing_result:
                if td.within_target:
                    text = f"{rate}, {self.cfg.text('landing_good', 'within target')}"
                else:
                    text = f"{rate}, {self.cfg.text('landing_over', 'over target')}"
                after.append(Callout("landing_result", text, 4.0))
            elif self.cfg.announce_touchdown:
                text = self.cfg.text("touchdown", f"touchdown, {number_words(round(abs(td.vs_fpm)))} feet per minute")
                after.append(Callout("touchdown", text, 3.0))
        elif not s.on_ground:
            self.landed = False

        self._track_final(s)
        if s.on_ground:
            self.speed_watch = False

        # Height callouts (crossing downward, armed, the plane was flying)
        if not prev.on_ground:
            crossed = [h for h in self.thresholds if self.armed[h] and prev.agl_ft > h >= s.agl_ft]
            if crossed:
                for h in crossed:
                    self.armed[h] = False
                allowed = [h for h in crossed if self._gear_ok(h, s)]
                if allowed:
                    h = min(allowed)
                    c = self._height_callout(h, s)
                    if c.key == "retard":
                        self.retard_active = True
                        self.last_retard_t = s.t
                        retard.append(c)
                    else:
                        heights.append(c)

        self._arm(s)

        # Retard repeat
        if self.retard_active and not retard:
            if s.on_ground or s.agl_ft > self.cfg.retard_ft + arm_margin(self.cfg.retard_ft):
                self.retard_active = False
            elif self.cfg.retard_repeat and s.throttle_pct is not None:
                if s.throttle_pct <= self.cfg.retard_idle_pct:
                    self.retard_active = False
                elif s.t - self.last_retard_t >= self.cfg.retard_repeat_s - EPS_S:
                    self.last_retard_t = s.t
                    retard.append(Callout("retard", self.cfg.text("retard"), 1.5))

        # Condition callouts
        if not s.on_ground:
            conditions = self._conditions(s)

        # Rollout
        if s.on_ground and self.landed:
            rollout_kt = set(self.cfg.rollout_callouts_kt) | ({DC6_REVERSE_CUE_KT} if self.dc6 else set())
            crossed_kt = [k for k in rollout_kt
                          if k not in self.rollout_done and prev.ias_kt > k >= s.ias_kt]
            if crossed_kt:
                self.rollout_done.update(crossed_kt)
                k = min(crossed_kt)
                after.append(Callout(f"rollout_{k}",
                                     self.cfg.text(f"rollout_{k}", f"{number_words(k)} knots"), 2.0))

        gates = self._approach_checks(prev, s, heights)
        landing_check = self._landing_check(s)
        takeoff = self._takeoff(prev, s)
        return takeoff + heights + gates + landing_check + retard + flaps + conditions + after

    # ----------------------------------------------------------------- helpers

    def _landing_check(self, s: Sample) -> list[Callout]:
        """DC-6: "flaps full, three green" (or "check flaps" ...), once per approach, passing 300 ft on final."""
        if s.on_ground or s.agl_ft > LANDING_CHECK_REARM_FT:
            self.landing_checked = False
            return []
        # on final and really descending (rising ground under a level airplane isn't a final; the user's
        # finals in every recording come down at 350-850 fpm)
        if (not (self.dc6 and self.cfg.landing_check) or self.landing_checked or not self.on_final
                or s.vs_fpm > -LANDING_CHECK_MIN_FPM):
            return []
        if s.agl_ft > LANDING_CHECK_FT:
            return []
        self.landing_checked = True
        missing = []
        if s.flaps_deg is not None and s.flaps_deg < DC6_FULL_FLAPS_MIN:
            missing.append(self.cfg.text("check_flaps", "check flaps"))
        if not s.gear_down:
            missing.append(self.cfg.text("check_gear", "check gear"))
        text = ", ".join(missing) or self.cfg.text("landing_check", "flaps full, three green")
        return [Callout("landing_check", text, 6.0)]

    def _takeoff(self, prev: Sample, s: Sample) -> list[Callout]:
        """ "vee one", "rotate", "positive rate" (+ optional "eighty knots", "vee two"), and in DC-6 mode a
        take-off flaps check as the roll starts. Armed when stopped/slow on the ground or on touchdown."""
        cfg, out = self.cfg, []
        if s.on_ground and (s.ias_kt < TAKEOFF_ARM_KT or (not prev.on_ground)):
            self.to_ready, self.to_said, self.to_lifted = True, set(), False
            self.to_flaps_checked = False
            self.to_from_slow = s.ias_kt < TAKEOFF_ARM_KT   # armed on touchdown: no flaps check (taxi, rollout)
        if not self.to_ready:
            return out
        if not s.on_ground and s.agl_ft > TAKEOFF_DONE_FT:
            self.to_ready = False                 # climbed away: take-off phase over
            return out

        # DC-6: flaps must be at 20 for take-off - checked once as the roll passes 40 kt
        if (self.dc6 and s.on_ground and self.to_from_slow and not self.to_flaps_checked
                and prev.ias_kt < TAKEOFF_FLAPS_CHECK_KT <= s.ias_kt):
            self.to_flaps_checked = True
            if s.flaps_deg is not None and not (DC6_TAKEOFF_FLAPS[0] <= s.flaps_deg <= DC6_TAKEOFF_FLAPS[1]):
                out.append(Callout("takeoff_flaps", cfg.text("takeoff_flaps", "check flaps"), 2.0))

        if cfg.takeoff_callouts and s.ias_kt > prev.ias_kt and (s.on_ground or s.agl_ft < TAKEOFF_CALLS_BELOW_FT):
            speeds = [(80, "eighty_knots", "eighty knots") if cfg.call_80kt else None,
                      (cfg.v1_kt, "v1", "vee one") if cfg.v1_kt else None,
                      (cfg.vr_kt, "rotate", "rotate") if cfg.vr_kt else None,
                      (cfg.v2_kt, "v2", "vee two") if cfg.call_v2 and cfg.v2_kt else None]
            crossed = [x for x in speeds if x and x[1] not in self.to_said and prev.ias_kt < x[0] <= s.ias_kt]
            if crossed:
                crossed.sort(key=lambda x: x[0])
                self.to_said.update(x[1] for x in crossed)
                text = ", ".join(cfg.text(key, words) for _, key, words in crossed)   # "vee one, rotate"
                out.append(Callout(crossed[-1][1], text, 1.5))

        if not s.on_ground and prev.on_ground:
            self.to_lifted = True
        if (cfg.takeoff_callouts and self.to_lifted and "positive_rate" not in self.to_said
                and s.vs_fpm > POSITIVE_RATE_FPM and s.agl_ft > POSITIVE_RATE_FT):
            self.to_said.add("positive_rate")
            out.append(Callout("positive_rate", cfg.text("positive_rate", "positive rate"), 2.0))
        return out

    def _approach_checks(self, prev: Sample, s: Sample, heights: list[Callout]) -> list[Callout]:
        """Wind check passing 1000 ft and stable check passing 500 ft, once per approach each."""
        out: list[Callout] = []
        if s.heading_deg is not None:
            self.heading_hist.append((s.t, s.heading_deg))
            while self.heading_hist and s.t - self.heading_hist[0][0] > HEADING_STEADY_S + 1:
                self.heading_hist.pop(0)
        approach = (not prev.on_ground and (s.gear_down or s.ias_kt < TOO_LOW_GEAR_MAX_KT)
                    and (self.on_final or s.vs_fpm < -FINAL_DESCENT_FPM))
        # The wind check is once per approach: re-armed by landing or by climbing back above 1000 ft.
        if s.on_ground or s.agl_ft > WIND_CHECK_FT + arm_margin(WIND_CHECK_FT):
            self.wind_done = False
            self.xwind_remarked = False
        final_started = self.on_final and not self.was_on_final
        self.was_on_final = self.on_final
        for name, h in (("wind", WIND_CHECK_FT), ("stable", STABLE_CHECK_FT)):
            crossed = self.gate_armed[name] and prev.agl_ft > h >= s.agl_ft
            if crossed:
                self.gate_armed[name] = False
            if s.agl_ft > h + arm_margin(h):
                self.gate_armed[name] = True
            if name == "wind":
                # Passing 1000 ft - or, on a low circuit that never got that high, starting down on final.
                low_final = final_started and s.gear_down and not prev.on_ground and s.agl_ft < h
                if self.cfg.wind_check and not self.wind_done and ((crossed and approach) or low_final):
                    c = self._wind_callout(s)
                    if c:
                        out.append(c)
                        self.wind_done = True
                continue
            # Stable check passing 500 ft, while actually descending: rising ground under a level or climbing
            # airplane once "crossed" 500 ft a minute before the real final (2026-10-09).
            if crossed and approach and s.vs_fpm < -FINAL_DESCENT_FPM and self.cfg.stable_check:
                verdict = self._stable_verdict(s)
                five = next((c for c in heights if c.key == "500"), None)
                if five is not None:              # "five hundred, stable" instead of "five hundred, speed ..."
                    five.text = f"{self.cfg.text('500', number_words(500))}, {verdict}"
                else:
                    out.append(Callout("stable", verdict, 2.0))
        late = self._late_crosswind(s, approach)
        if late:
            out.append(late)
        return out

    def _heading_steady(self, s: Sample) -> bool:
        """Has the heading stayed within HEADING_STEADY_DEG over the last HEADING_STEADY_S?"""
        recent = [h for t, h in self.heading_hist if s.t - t <= HEADING_STEADY_S + EPS_S]
        if not recent or s.t - self.heading_hist[0][0] < HEADING_STEADY_S - EPS_S:
            return False                          # not enough history yet
        return all(abs((h - s.heading_deg + 180) % 360 - 180) <= HEADING_STEADY_DEG for h in recent)

    def _stable_verdict(self, s: Sample) -> str:
        cfg, reasons = self.cfg, []
        if not s.gear_down:
            reasons.append("gear")
        if s.flaps_deg is not None and s.flaps_deg < STABLE_MIN_FLAPS_DEG:
            reasons.append("flaps")
        if self.cfg.vref_kt > 0:
            d = s.ias_kt - self.cfg.vref_kt
            if (cfg.speed_plus_kt and d >= min(cfg.speed_plus_kt)) or (cfg.speed_minus_kt and d <= -min(cfg.speed_minus_kt)):
                reasons.append("speed")
        if cfg.sink_rate_fpm > 0 and s.vs_fpm < -cfg.sink_rate_fpm:
            reasons.append("sink rate")
        if not reasons:
            return cfg.text("stable", "stable")
        return f"{cfg.text('unstable', 'unstable')}, {', '.join(reasons)}"

    def _wind_callout(self, s: Sample) -> Callout | None:
        if s.wind_kt is None or s.wind_dir_deg is None:
            return None
        if s.wind_kt < WIND_CALM_KT:
            return Callout("wind", self.cfg.text("wind_calm", "wind calm"), 3.0)
        direction = int(math.floor(s.wind_dir_deg / 10 + 0.5)) * 10 % 360 or 360   # 165 -> 170 (halves up)
        spoken_dir = " ".join(digit_words(int(c)) for c in f"{direction:03d}")   # 090 -> "zero nine zero"
        parts = [f"wind {spoken_dir} at {number_words(round(s.wind_kt))}"]
        if s.heading_deg is not None and self._heading_steady(s):   # mid-turn the crosswind would be wrong
            rel = math.radians(s.wind_dir_deg - s.heading_deg)
            cross, head = s.wind_kt * math.sin(rel), s.wind_kt * math.cos(rel)
            if abs(cross) >= WIND_CALM_KT:
                parts.append(f"crosswind {number_words(round(abs(cross)))} from the {'right' if cross > 0 else 'left'}")
            if head <= -WIND_CALM_KT:
                parts.append(f"tailwind {number_words(round(-head))}")
            remark = self._crosswind_remark(cross)
            if remark:
                return Callout("wind", ", ".join(parts) + ". " + remark, 4.0)
        return Callout("wind", ", ".join(parts), 3.0)

    def _crosswind_remark(self, cross: float) -> str | None:
        """A bit of crew personality for a strong crosswind - once per approach, lines in rotation."""
        global _remark_count
        if not self.cfg.crosswind_remarks or self.xwind_remarked or abs(cross) < XWIND_REMARK_KT:
            return None
        self.xwind_remarked = True
        line = XWIND_REMARKS[_remark_count % len(XWIND_REMARKS)]
        _remark_count += 1
        return self.cfg.text("crosswind_remark", line)

    def _late_crosswind(self, s: Sample, approach: bool) -> Callout | None:
        """If the wind check couldn't work out the crosswind (still turning), say it once lined up on final."""
        if (self.xwind_remarked or not self.wind_done or not self.cfg.crosswind_remarks or not approach
                or not self.on_final
                or not XWIND_LATE_MIN_FT < s.agl_ft < XWIND_LATE_MAX_FT or s.wind_kt is None
                or s.wind_dir_deg is None or s.heading_deg is None or not self._heading_steady(s)):
            return None
        cross = s.wind_kt * math.sin(math.radians(s.wind_dir_deg - s.heading_deg))
        remark = self._crosswind_remark(cross)
        if not remark:
            return None
        side = "right" if cross > 0 else "left"
        return Callout("crosswind_remark", f"crosswind {number_words(round(abs(cross)))} from the {side}. {remark}", 4.0)

    def _flaps(self, prev: Sample, s: Sample) -> list[Callout]:
        """ "flaps thirty" once the flaps have stopped at a new setting (angle rounded to 5 degrees)."""
        f = s.flaps_deg
        if not self.cfg.flap_callouts or f is None:
            return []
        if self.flaps_reported is None:
            self.flaps_reported = f
        if prev.flaps_deg is None or abs(f - prev.flaps_deg) > FLAP_MOVING_DEG:
            self.flaps_moved_t = s.t
        elif abs(f - self.flaps_reported) >= FLAP_CHANGE_DEG and s.t - self.flaps_moved_t >= FLAP_SETTLE_S - EPS_S:
            self.flaps_reported = f
            setting = int(round(f / 5) * 5)
            text = "flaps up" if setting == 0 else f"flaps {number_words(setting)}"
            return [Callout(f"flaps_{setting}", self.cfg.text(f"flaps_{setting}", text), 2.0)]
        return []

    def _track_final(self, s: Sample) -> None:
        """Are we on final (descending toward a landing)? Takeoff, climbs, go-arounds and level legs: no."""
        if s.on_ground:
            self.on_final, self.descent_since = False, None
            return
        if s.vs_fpm < -FINAL_ENTRY_FPM:
            if self.descent_since is None:
                self.descent_since = s.t
            if s.t - self.descent_since >= FINAL_ENTRY_S - EPS_S:
                self.on_final = True
        else:
            self.descent_since = None
        if self.on_final and s.vs_fpm < -FINAL_DESCENT_FPM:
            self.last_descent_t = s.t
        if self.on_final and (s.vs_fpm > CLIMBING_FPM or s.t - self.last_descent_t > FINAL_MEMORY_S + EPS_S):
            self.on_final = False

    def _arm(self, s: Sample) -> None:
        for h in self.thresholds:
            if s.agl_ft > h + arm_margin(h):
                self.armed[h] = True

    def _gear_ok(self, h: int, s: Sample) -> bool:
        """Gear gate. Heights above 1000 ft (e.g. 2500) come before gear-down on a normal approach,
        so they may also fire while clearly descending. Level flight over rising terrain stays quiet."""
        if s.gear_down or not self.cfg.require_gear_down:
            return True
        return h > GEAR_UP_OK_ABOVE_FT and s.vs_fpm < -GEAR_UP_MIN_DESCENT_FPM

    def _teleport(self, s: Sample) -> None:
        for h in self.thresholds:
            if h >= s.agl_ft:
                self.armed[h] = False
        self._arm(s)
        self.retard_active = False
        self.landed = False
        self.on_final, self.descent_since, self.last_descent_t = False, None, float("-inf")
        self.gate_armed = {"wind": s.agl_ft > WIND_CHECK_FT + arm_margin(WIND_CHECK_FT),
                           "stable": s.agl_ft > STABLE_CHECK_FT + arm_margin(STABLE_CHECK_FT)}
        self.flaps_reported = s.flaps_deg

    def _height_callout(self, h: int, s: Sample) -> Callout:
        key = self.thresholds[h]
        if key.isdigit():
            text = self.cfg.text(key, number_words(h))
            if h in self.cfg.speed_readout_heights:
                text = f"{text}, speed {digit_words(s.ias_kt)}"
            max_age = 0.8 if h <= 100 else 1.5
        else:
            text = self.cfg.text(key)
            max_age = 1.5
        return Callout(key, text, max_age)

    def _conditions(self, s: Sample) -> list[Callout]:
        cfg = self.cfg
        active: list[tuple[str, float]] = []
        in_window = 50 < s.agl_ft < 1000
        # Speed is only judged against Vref on final: gear down, below 1000 ft and descending (now or
        # within FINAL_MEMORY_S). Takeoff, climb-out, go-arounds and level downwind legs stay quiet.
        self.speed_watch = self.cfg.vref_kt > 0 and s.gear_down and in_window and self.on_final
        # DC-6: not until landing flaps (40 or full) are set. At flaps 20 you're still slowing down, and
        # "plus sixty" at 1000 ft while turning onto final didn't help. A missing flaps reading doesn't block.
        if self.dc6 and s.flaps_deg is not None and s.flaps_deg < DC6_LANDING_FLAPS_MIN:
            self.speed_watch = False

        if cfg.overspeed_warnings:
            if s.flap_overspeed:
                active.append(("flap_speed", 1.5))
            if s.gear_overspeed:
                active.append(("gear_speed", 1.5))
        # On final only: a climb-out over a ridge drops the height above ground, and easing over at the
        # crest once called "too low, gear" a minute after takeoff.
        if (cfg.gear_warning_ft > 0 and s.agl_ft < cfg.gear_warning_ft and not s.gear_down and s.vs_fpm < -200
                and s.ias_kt < TOO_LOW_GEAR_MAX_KT and self.on_final):
            active.append(("too_low_gear", 1.5))
        if cfg.sink_rate_fpm > 0 and in_window and s.vs_fpm < -cfg.sink_rate_fpm:
            active.append(("sink_rate", 2.0))
        if cfg.speed_callouts == "warnings" and self.speed_watch:
            if s.ias_kt < self.cfg.vref_kt - cfg.speed_low_margin_kt:
                active.append(("speed_low", 2.0))
            elif cfg.speed_high_margin_kt > 0 and s.ias_kt > self.cfg.vref_kt + cfg.speed_high_margin_kt:
                active.append(("speed_high", 2.0))

        out = []
        for key, max_age in active:
            last = self.last_condition_t.get(key)
            if last is None or s.t - last >= cfg.repeat_interval_s - EPS_S:
                self.last_condition_t[key] = s.t
                out.append(Callout(key, cfg.text(key), max_age))
        if cfg.speed_callouts == "deviation":
            out += self._deviation(s)
        return out

    def _deviation(self, s: Sample) -> list[Callout]:
        """ "plus ten" / "minus five" when the speed band relative to Vref changes, and "ref" when back
        on speed after one of those. Silent on a steady on-speed approach.

        Getting worse (+10 -> +15, or flipping sides) is called right away (calls DEV_MIN_GAP_S apart).
        Getting better (+20 -> +15, or back on speed) only counts once it has held DEV_IMPROVE_HOLD_S,
        and a band is kept until the speed is DEV_HYSTERESIS_KT back inside it. So a needle wobbling
        across a threshold in gusts doesn't cause a string of calls.
        """
        cfg = self.cfg
        if not self.speed_watch:
            self.dev_pending = None
            if self.off_final_t is None:
                self.off_final_t = s.t
            elif s.t - self.off_final_t > DEV_BAND_MEMORY_S:
                self.dev_band = 0                # a later approach announces its band fresh
            return []
        self.off_final_t = None
        d = s.ias_kt - self.cfg.vref_kt
        # Steps, plus tens past the top step, plus the current band (so its hysteresis still applies).
        plus = sorted(set(cfg.speed_plus_kt) | set(_beyond(d, cfg.speed_plus_kt))
                      | ({self.dev_band} if self.dev_band > 0 else set()))
        minus = sorted(set(cfg.speed_minus_kt) | set(_beyond(-d, cfg.speed_minus_kt))
                       | ({-self.dev_band} if self.dev_band < 0 else set()))
        band = 0
        for t in plus:
            if d >= t or (self.dev_band == t and d > t - DEV_HYSTERESIS_KT):
                band = t
        for t in minus:
            if d <= -t or (self.dev_band == -t and d < -t + DEV_HYSTERESIS_KT):
                band = -t
        if band == self.dev_band:
            self.dev_pending = None
            return []
        worse = band != 0 and (abs(band) > abs(self.dev_band) or (band > 0) != (self.dev_band > 0))
        top = max(cfg.speed_plus_kt or [0]) if band > 0 else max(cfg.speed_minus_kt or [0])
        if not worse and band != 0 and abs(band) > top:
            # Slowing from way off (+50 -> +40 -> +30): follow quietly; speak again at the top step (+20).
            self.dev_band, self.dev_pending = band, None
            return []
        if not worse:
            if self.dev_pending != band:
                self.dev_pending, self.dev_pending_t = band, s.t
                return []
            if s.t - self.dev_pending_t < DEV_IMPROVE_HOLD_S - EPS_S:
                return []
            self.dev_pending = None
        if s.t - self.last_dev_t < DEV_MIN_GAP_S - EPS_S:
            return []                            # try again on a later reading
        self.dev_band, self.last_dev_t, self.dev_pending = band, s.t, None
        if band == 0:                            # back on speed after a deviation call (held 5 s)
            return [Callout("speed_ref", cfg.text("speed_ref", "ref"), 2.0)]
        key = f"speed_plus_{band}" if band > 0 else f"speed_minus_{-band}"
        text = f"{'plus' if band > 0 else 'minus'} {number_words(abs(band))}"
        return [Callout(key, cfg.text(key, text), 2.0)]
