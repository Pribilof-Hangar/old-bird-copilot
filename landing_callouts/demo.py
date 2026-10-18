"""A fake approach so the voice and timing can be heard without the sim."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

from .engine import Sample

HZ = 10


def approach_samples(start_t: float = 0.0) -> Iterator[Sample]:
    """1500 ft at 130 kt and -700 fpm, flare at 40 ft, touch down, roll out to 30 kt.

    Flaps follow the DC-6 technique: 30 at the start, 40 on the glide path, 50 before 400 ft.
    """
    dt = 1.0 / HZ
    t = start_t
    agl, ias, vs, throttle, flaps = 1500.0, 130.0, -700.0, 40.0, 30.0

    while agl > 0:
        yield Sample(t, agl, ias, vs, on_ground=False, gear_down=True, throttle_pct=throttle, flaps_deg=flaps)
        t += dt
        target = 50.0 if agl < 450 else 40.0 if agl < 1300 else 30.0
        flaps = min(target, flaps + 10 * dt) if flaps < target else flaps  # flaps run at 10 degrees/s
        if agl < 40:
            vs += (-150.0 - vs) * 0.15      # flare: ease toward -150 fpm
        if agl <= 12:
            throttle = 0.0
        ias = max(108.0, ias - 0.15)        # bleed toward about Vref
        agl += vs / 60.0 * dt

    while ias > 30:
        yield Sample(t, 0.0, ias, 0.0, on_ground=True, gear_down=True, throttle_pct=0.0, flaps_deg=flaps)
        t += dt
        ias -= 3.0 * dt
    yield Sample(t, 0.0, 30.0, 0.0, on_ground=True, gear_down=True, throttle_pct=0.0, flaps_deg=flaps)


def run_demo(feed: Callable[[Sample], None], speed: float = 1.0, stop: threading.Event | None = None) -> bool:
    """Call feed(sample) for the fake approach in (scaled) real time. False if stopped early."""
    stop = stop or threading.Event()
    t0 = time.monotonic()
    for s in approach_samples():
        delay = t0 + s.t / speed - time.monotonic()
        if delay > 0 and stop.wait(delay):
            return False
        if stop.is_set():
            return False
        # Hand the engine real time so the speaker's age checks line up.
        s.t = time.monotonic()
        feed(s)
    return True
