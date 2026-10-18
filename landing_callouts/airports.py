"""Airport lookup by code (PASN, PAKN, Z13 ...) from the built-in OurAirports list (public domain)."""

from __future__ import annotations

import csv
import gzip
import io
import math
import threading
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "airports.csv.gz"
EARTH_NM = 3440.065


@dataclass(frozen=True)
class Airport:
    code: str          # the code it was found by
    name: str
    lat: float
    lon: float
    elev_ft: int


_index: dict[str, tuple[str, float, float, int]] | None = None
_lock = threading.Lock()


def _load() -> dict[str, tuple[str, float, float, int]]:
    global _index
    with _lock:
        if _index is None:
            index: dict[str, tuple[str, float, float, int]] = {}
            with gzip.open(DATA, "rb") as raw:
                for codes, name, lat, lon, elev in csv.reader(io.TextIOWrapper(raw, encoding="utf-8")):
                    entry = (name, float(lat), float(lon), int(elev))
                    for i, code in enumerate(codes.split()):
                        if i == 0 or code not in index:   # an airport's own ident wins over another's local code
                            index[code] = entry
            _index = index
        return _index


def find(code: str) -> Airport | None:
    code = code.strip().upper()
    if not code:
        return None
    entry = _load().get(code)
    return Airport(code, *entry) if entry else None


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_NM * math.asin(min(1.0, math.sqrt(a)))
