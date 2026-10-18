"""Suggested Vref from live weight, using the aircraft's own landing-speed table.

DC-6 numbers: PMDG DC-6 POH (August 2021), Section VIII p.318, "VAT - target threshold speed" (KIAS)
by landing weight and flap setting. Only aircraft whose title matches get a suggestion; nothing is
guessed for other aircraft.
"""

from __future__ import annotations

from dataclasses import dataclass

DC6_WEIGHTS = [70, 72, 74, 76, 78, 80, 82, 84, 86, 88]          # x 1000 lb
DC6_VAT = {
    "full": [94, 95, 97, 98, 99, 100, 102, 103, 104, 106],
    "40": [101, 103, 104, 105, 106, 107, 108, 109, 110, 111],
    "30/20": [108, 110, 111, 112, 113, 114, 115, 116, 117, 118],
    "0": [118, 120, 121, 122, 123, 124, 125, 126, 127, 128],
}


@dataclass(frozen=True)
class Suggestion:
    weight_lb: float
    full: int            # Vref at flaps full
    flaps40: int         # Vref at flaps 40
    note: str = ""       # "below table" / "over max landing wt"


def is_dc6(title: str | None) -> bool:
    if not title:
        return False
    t = title.upper().replace(" ", "").replace("-", "")
    return "DC6" in t


def suggest(title: str | None, weight_lb: float | None) -> Suggestion | None:
    """The DC-6 table value for this weight, or None if it isn't a DC-6 or the weight is unknown."""
    if weight_lb is None or weight_lb <= 0 or not is_dc6(title):
        return None
    w = weight_lb / 1000
    note = ""
    if w < DC6_WEIGHTS[0]:
        w, note = DC6_WEIGHTS[0], "below table"
    elif w > DC6_WEIGHTS[-1]:
        w, note = DC6_WEIGHTS[-1], "over max landing wt"
    return Suggestion(weight_lb, round(_interp(w, DC6_VAT["full"])), round(_interp(w, DC6_VAT["40"])), note)


def _interp(w: float, speeds: list[int], weights: list[float] = DC6_WEIGHTS) -> float:
    for i in range(len(weights) - 1):
        lo, hi = weights[i], weights[i + 1]
        if lo <= w <= hi:
            return speeds[i] + (speeds[i + 1] - speeds[i]) * (w - lo) / (hi - lo)
    return float(speeds[-1])


# Take-off speeds, same POH page: autofeather operative. VR = V2 - 5, VF (flaps up) = V2 + 15.
DC6_TOW = [80, 82, 84, 86, 88, 90, 92, 94, 96, 98, 100, 103]               # x 1000 lb
DC6_V1_WET = [80, 83, 85, 88, 90, 93, 95, 97, 99, 102, 105, 107]
DC6_V2 = [101, 103, 104, 106, 107, 108, 109, 110, 111, 112, 114, 115]
DC6_V1_DRY_TOW = [80, 82, 84, 86, 88, 90, 92]
DC6_V1_DRY = [85, 88, 90, 93, 96, 99, 101]
DC6_DRY_MAX_TOW_LB = 87_600            # above this a wet (water injection) take-off is needed
DC6_VMCG = 78


@dataclass(frozen=True)
class TakeoffSpeeds:
    weight_lb: float
    v1: int
    vr: int
    v2: int
    wet: bool
    note: str = ""


def takeoff(title: str | None, weight_lb: float | None, elevation_ft: float | None = None,
            headwind_kt: float | None = None) -> TakeoffSpeeds | None:
    """DC-6 V1/VR/V2 for this weight, with the POH corrections to V1 for elevation and wind."""
    if weight_lb is None or weight_lb <= 0 or not is_dc6(title):
        return None
    wet = weight_lb > DC6_DRY_MAX_TOW_LB
    w = weight_lb / 1000
    note = ""
    if w < DC6_TOW[0]:
        w, note = DC6_TOW[0], "below table"
    elif w > DC6_TOW[-1]:
        w, note = DC6_TOW[-1], "over max take-off wt"
    v2 = _interp(w, DC6_V2, DC6_TOW)
    if wet:
        v1 = _interp(w, DC6_V1_WET, DC6_TOW)
    else:
        v1 = _interp(min(w, DC6_V1_DRY_TOW[-1]), DC6_V1_DRY, DC6_V1_DRY_TOW)
    # POH corrections to V1: +1 kt per 1000 ft elevation, +1 per 15 kt headwind, -2 per 5 kt tailwind.
    if elevation_ft:
        v1 += max(elevation_ft, 0) / 1000
    if headwind_kt:
        v1 += headwind_kt / 15 if headwind_kt > 0 else headwind_kt / 5 * 2
    vr = round(v2) - 5
    v1 = max(DC6_VMCG, min(round(v1), vr))   # never below Vmcg, never above VR
    return TakeoffSpeeds(weight_lb, v1, vr, round(v2), wet, note)
