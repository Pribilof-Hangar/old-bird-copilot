"""Build landing_callouts/data/airports.csv.gz from OurAirports' airports.csv (public domain).

    python tools/make_airports.py path/to/airports.csv

Download: https://davidmegginson.github.io/ourairports-data/airports.csv
Keeps airports and seaplane bases (no heliports, balloon ports or closed fields). Each row:
codes (ident, ICAO, GPS and local codes, space separated), name, latitude, longitude, elevation in ft.
"""

import csv
import gzip
import sys
from pathlib import Path

KEEP_TYPES = {"large_airport", "medium_airport", "small_airport", "seaplane_base"}
OUT = Path(__file__).resolve().parent.parent / "landing_callouts" / "data" / "airports.csv.gz"


def main(src: str) -> None:
    rows = []
    with open(src, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["type"] not in KEEP_TYPES or not r["elevation_ft"]:
                continue
            codes = []
            for key in ("ident", "icao_code", "gps_code", "local_code"):
                c = r[key].strip().upper()
                if c and c not in codes:
                    codes.append(c)
            rows.append([" ".join(codes), r["name"].strip(), f"{float(r['latitude_deg']):.5f}",
                         f"{float(r['longitude_deg']):.5f}", str(int(float(r["elevation_ft"])))])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    print(f"{len(rows)} airports -> {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1])
