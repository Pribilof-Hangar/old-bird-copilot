"""Configuration: dataclass with defaults, TOML loading and validation."""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TEXTS: dict[str, str] = {
    "retard": "retard",
    "minimums": "minimums",
    "approaching_minimums": "approaching minimums",
    "speed_low": "speed low",
    "speed_high": "speed high",
    "sink_rate": "sink rate",
    "too_low_gear": "too low, gear",
    "flap_speed": "flap speed",
    "gear_speed": "gear speed",
}
# Keys (besides height numbers and "rollout_<kt>") that [texts] may override.
TEXT_KEYS = set(DEFAULT_TEXTS) | {"touchdown", "flaps_up", "landing_good", "landing_over", "speed_ref",
                                  "stable", "unstable", "wind_calm", "v1", "rotate", "v2", "eighty_knots",
                                  "positive_rate", "takeoff_flaps", "crosswind_remark", "top_of_descent",
                                  "high_on_profile", "landing_check", "check_flaps", "check_gear", "level_off",
                                  "request_descent"}
SPEED_MODES = ("deviation", "warnings")
# Settings from older versions: ignored without a warning, and dropped on the next save.
RETIRED_KEYS = {"auto_flaps", "aircraft"}

# Callout styles. Picking one in Settings fills in these values; "custom" means the user edited them.
STYLES = ("boeing", "airbus", "custom")
STYLE_PRESETS: dict[str, dict] = {
    "boeing": {"heights": [2500, 1000, 500, 100, 50, 40, 30, 20, 10], "retard_ft": 0},
    "airbus": {"heights": [2500, 1000, 500, 400, 300, 200, 100, 50, 40, 30, 20, 10], "retard_ft": 10},
}
# Wording per style, used unless [texts] overrides it. "custom" uses Boeing wording.
STYLE_TEXTS: dict[str, dict[str, str]] = {
    "boeing": {"2500": "twenty five hundred"},
    "airbus": {"approaching_minimums": "hundred above", "minimums": "minimum"},
}


def style_of(heights: list[int], retard_ft: int) -> str:
    """The preset these values match, or "custom"."""
    for name, preset in STYLE_PRESETS.items():
        if sorted(heights, reverse=True) == preset["heights"] and retard_ft == preset["retard_ft"]:
            return name
    return "custom"


class ConfigError(Exception):
    """A config value is invalid. The message is meant for the user."""


@dataclass
class Config:
    # "flaps thirty" when the flaps settle at a new setting
    flap_callouts: bool = True

    # Main-window switch: off for aircraft that call heights themselves (keeps all other callouts)
    height_callouts: bool = True
    # Approach checks: wind passing 1000 ft, "stable / unstable" passing 500 ft
    wind_check: bool = True
    crosswind_remarks: bool = True      # "these are some crosswinds!" when 25 kt or more across
    stable_check: bool = True
    landing_check: bool = True          # DC-6: "flaps full, three green" / "check flaps", passing 300 ft on final

    # Take-off: "vee one", "rotate", "positive rate" (V-speeds per flight, or from the DC-6 table)
    takeoff_callouts: bool = True
    call_80kt: bool = False
    call_v2: bool = False
    v1_kt: int = 0
    vr_kt: int = 0
    v2_kt: int = 0

    # Style ("boeing", "airbus" or "custom") and heights
    callout_style: str = "boeing"
    heights: list[int] = field(default_factory=lambda: list(STYLE_PRESETS["boeing"]["heights"]))
    require_gear_down: bool = True

    # Minimums
    minimums_ft: int = 0
    approaching_minimums: bool = True

    # Retard (0 = off, the Boeing default)
    retard_ft: int = 0
    retard_repeat: bool = True
    retard_repeat_s: float = 1.5
    retard_idle_pct: float = 10

    # Speed
    speed_readout_heights: list[int] = field(default_factory=lambda: [1000, 500])
    vref_kt: int = 0
    # "deviation": "plus ten" / "minus five" when the speed band changes. "warnings": "speed high" / "speed low"
    speed_callouts: str = "deviation"
    speed_plus_kt: list[int] = field(default_factory=lambda: [20, 15, 10])
    speed_minus_kt: list[int] = field(default_factory=lambda: [10, 5])
    speed_low_margin_kt: int = 5
    speed_high_margin_kt: int = 20

    # Warnings
    sink_rate_fpm: int = 1000
    gear_warning_ft: int = 500
    overspeed_warnings: bool = True     # "flap speed" / "gear speed" from the aircraft's own limits
    repeat_interval_s: float = 5

    # Descent planning (needs a destination in the main window): "top of descent", "high on profile"
    descent_calls: bool = True
    request_descent_call: bool = True    # "request descent" 2 minutes before the top of descent (for ATC)
    descent_rate_fpm: int = 1000         # planned descent rate (DC-6 mode uses 500); slide it in the calculator
    descent_pattern_ft: int = 1500       # be at this height above the field ...
    descent_margin_nm: int = 5           # ... this far out, to slow down and configure

    # Touchdown / rollout
    landing_target_fpm: int = 0         # max touchdown rate (e.g. 200 for a NeoFly mission). 0 = off
    speak_landing_result: bool = True    # "one eight two, within target" after touchdown (when a target is set)
    announce_touchdown: bool = False
    rollout_callouts_kt: list[int] = field(default_factory=list)

    # Voice
    voice: str = ""
    rate: int = 2
    volume: int = 100
    sounds_dir: str = "sounds"

    # Sim
    agl_offset_ft: float = 0
    throttle_simvar: str = "GENERAL ENG THROTTLE LEVER POSITION:1"

    # Flight recorder (flights/ folder, newest 20 sessions kept)
    record_flights: bool = True

    texts: dict[str, str] = field(default_factory=dict)

    # Folder the config file was loaded from (not a TOML key). Relative paths resolve against it.
    base_dir: Path = field(default_factory=Path.cwd, repr=False)

    def text(self, key: str, default: str | None = None) -> str:
        if key in self.texts:
            return self.texts[key]
        style_texts = STYLE_TEXTS.get(self.callout_style, STYLE_TEXTS["boeing"])
        if key in style_texts:
            return style_texts[key]
        if key in DEFAULT_TEXTS:
            return DEFAULT_TEXTS[key]
        return default if default is not None else key

    @property
    def sounds_path(self) -> Path:
        p = Path(self.sounds_dir)
        return p if p.is_absolute() else self.base_dir / p


# key -> (kind, min, max). kind is one of: bool, int, float, str, style, intlist, texts
_SCHEMA: dict[str, tuple[str, float | None, float | None]] = {
    "flap_callouts": ("bool", None, None),
    "height_callouts": ("bool", None, None),
    "wind_check": ("bool", None, None),
    "crosswind_remarks": ("bool", None, None),
    "stable_check": ("bool", None, None),
    "landing_check": ("bool", None, None),
    "takeoff_callouts": ("bool", None, None),
    "call_80kt": ("bool", None, None),
    "call_v2": ("bool", None, None),
    "v1_kt": ("int", 0, 300),
    "vr_kt": ("int", 0, 300),
    "v2_kt": ("int", 0, 300),
    "callout_style": ("style", None, None),
    "heights": ("intlist", 1, 50_000),
    "require_gear_down": ("bool", None, None),
    "minimums_ft": ("int", 0, 50_000),
    "approaching_minimums": ("bool", None, None),
    "retard_ft": ("int", 0, 200),
    "retard_repeat": ("bool", None, None),
    "retard_repeat_s": ("float", 0.3, 30),
    "retard_idle_pct": ("float", 0, 100),
    "speed_readout_heights": ("intlist", 1, 50_000),
    "vref_kt": ("int", 0, 400),
    "speed_callouts": ("speedmode", None, None),
    "speed_plus_kt": ("intlist", 1, 100),
    "speed_minus_kt": ("intlist", 1, 100),
    "speed_low_margin_kt": ("int", 0, 100),
    "speed_high_margin_kt": ("int", 0, 200),
    "sink_rate_fpm": ("int", 0, 10_000),
    "gear_warning_ft": ("int", 0, 5_000),
    "overspeed_warnings": ("bool", None, None),
    "repeat_interval_s": ("float", 0.5, 600),
    "descent_calls": ("bool", None, None),
    "request_descent_call": ("bool", None, None),
    "descent_rate_fpm": ("int", 100, 5000),
    "descent_pattern_ft": ("int", 0, 10_000),
    "descent_margin_nm": ("int", 0, 50),
    "landing_target_fpm": ("int", 0, 2000),
    "speak_landing_result": ("bool", None, None),
    "announce_touchdown": ("bool", None, None),
    "rollout_callouts_kt": ("intlist", 1, 400),
    "voice": ("str", None, None),
    "rate": ("int", -10, 10),
    "volume": ("int", 0, 100),
    "sounds_dir": ("str", None, None),
    "agl_offset_ft": ("float", -100, 100),
    "throttle_simvar": ("str", None, None),
    "record_flights": ("bool", None, None),
    "texts": ("texts", None, None),
}


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check(key: str, value, kind: str, lo, hi):
    def range_ok(x):
        if (lo is not None and x < lo) or (hi is not None and x > hi):
            raise ConfigError(f"'{key}' = {x} is out of range ({lo} to {hi})")

    if kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError(f"'{key}' must be true or false, got {value!r}")
        return value
    if kind == "int":
        if not _is_number(value) or value != int(value):
            raise ConfigError(f"'{key}' must be a whole number, got {value!r}")
        range_ok(value)
        return int(value)
    if kind == "float":
        if not _is_number(value):
            raise ConfigError(f"'{key}' must be a number, got {value!r}")
        range_ok(value)
        return float(value)
    if kind == "str":
        if not isinstance(value, str):
            raise ConfigError(f"'{key}' must be text in quotes, got {value!r}")
        return value
    if kind == "speedmode":
        if value not in SPEED_MODES:
            raise ConfigError(f"'{key}' must be one of {', '.join(repr(m) for m in SPEED_MODES)}, got {value!r}")
        return value
    if kind == "style":
        if value not in STYLES:
            raise ConfigError(f"'{key}' must be one of {', '.join(repr(s) for s in STYLES)}, got {value!r}")
        return value
    if kind == "intlist":
        if not isinstance(value, list):
            raise ConfigError(f"'{key}' must be a list like [1000, 500], got {value!r}")
        out = []
        for item in value:
            if not _is_number(item) or item != int(item):
                raise ConfigError(f"'{key}' must only contain whole numbers, got {item!r}")
            range_ok(item)
            out.append(int(item))
        return sorted(set(out), reverse=True)
    if kind == "texts":
        if not isinstance(value, dict):
            raise ConfigError("[texts] must be a table of key = \"text\" lines")
        out = {}
        for k, v in value.items():
            if not isinstance(v, str):
                raise ConfigError(f"[texts] {k} must be text in quotes, got {v!r}")
            numbered = ("rollout_", "flaps_", "speed_plus_", "speed_minus_")
            if not (k in TEXT_KEYS or k.isdigit()
                    or any(k.startswith(p) and k[len(p):].isdigit() for p in numbered)):
                warn(f"[texts] unknown key '{k}' (ignored unless it matches a callout)")
            out[k] = v
        return out
    raise AssertionError(kind)


def _print_warning(msg: str) -> None:
    print(f"Config warning: {msg}", file=sys.stderr)


# Replaced by the GUI so warnings show up in its activity log.
warning_handler = _print_warning


def warn(msg: str) -> None:
    warning_handler(msg)


def from_dict(data: dict, base_dir: Path | None = None) -> Config:
    kwargs = {}
    for key, value in data.items():
        if key in RETIRED_KEYS:
            continue
        if key not in _SCHEMA:
            warn(f"unknown setting '{key}' (ignored)")
            continue
        kind, lo, hi = _SCHEMA[key]
        kwargs[key] = _check(key, value, kind, lo, hi)
    cfg = Config(**kwargs)
    if base_dir is not None:
        cfg.base_dir = base_dir
    return cfg


def load(path: str | Path) -> Config:
    path = Path(path)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None
    except OSError as e:
        raise ConfigError(f"Couldn't read {path}: {e}") from None
    return from_dict(data, base_dir=path.resolve().parent)


DATA_FOLDER = "Old Bird Copilot"


def user_data_dir() -> Path:
    """Per-user folder for settings, flight recordings and the error log (APPDATA / Old Bird Copilot)."""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / DATA_FOLDER


def find_config() -> Path | None:
    """The settings file in use.

    Installed app: config.toml in user_data_dir(). Portable (zip) copy: it ships with a config.toml
    next to the exe, which keeps that copy self-contained. Running from source: the current folder or checkout.
    """
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "config.toml")   # portable copy
        candidates.append(user_data_dir() / "config.toml")
    else:
        candidates.append(Path.cwd() / "config.toml")
        candidates.append(Path(__file__).resolve().parent.parent / "config.toml")
    return next((p for p in candidates if p.is_file()), None)


def default_config_path() -> Path:
    """Where to create config.toml when there isn't one yet (the installed app's first start)."""
    if getattr(sys, "frozen", False):
        return user_data_dir() / "config.toml"
    return Path.cwd() / "config.toml"


# Written above each setting by dump(). Settings without a comment are written bare.
_COMMENTS = {
    "flap_callouts": "# Say \"flaps thirty\" etc. when the flaps settle at a new setting",
    "height_callouts": "# Height countdown (2500 ... 10, plus retard). Turn off for aircraft that call heights themselves;\n"
                       "# everything else (speed, flaps, warnings, minimums, landing) keeps working",
    "wind_check": "# Passing 1000 ft on approach: \"wind two seven zero at fifteen, crosswind ten from the left\"",
    "stable_check": "# Passing 500 ft: \"five hundred, stable\" or \"five hundred, unstable, speed\" (gear, flaps, speed\n"
                    "# within Vref -5/+10, sink rate)",
    "landing_check": "# DC-6 mode: \"flaps full, three green\" (or \"check flaps\" / \"check gear\") passing 300 ft on final",
    "takeoff_callouts": "\n# Take-off: \"vee one\", \"rotate\", \"positive rate\"; optional \"eighty knots\" and \"vee two\".\n"
                        "# V-speeds are normally set per flight in the main window (0 = that call off)",
    "callout_style": "\n# Callout style: \"boeing\", \"airbus\" or \"custom\". Sets the wording (e.g. Airbus \"minimum\",\n"
                     "# \"hundred above\"); pick it in Settings to also fill in the Boeing/Airbus heights and retard",
    "heights": "# Heights (ft above ground) to call out while descending",
    "require_gear_down": "# Height callouts only with the gear handle down (no callouts over hills in cruise).\n"
                         "# Heights above 1000 ft (e.g. 2500) also fire gear-up while clearly descending",
    "minimums_ft": "\n# Minimums (ft above ground). 0 = off",
    "approaching_minimums": "# \"approaching minimums\" at minimums + 100 ft",
    "retard_ft": "\n# Retard (Airbus). 0 = off (Boeing). Repeats while the throttle is above idle and still airborne",
    "speed_readout_heights": "\n# Speed. \"five hundred, speed one one two\" at these heights",
    "vref_kt": "# Default Vref. 0 = off. Depends on landing weight; see the aircraft's tables, or use touchdown speed + 5-10 kt",
    "speed_callouts": "# Speed on final (gear down, 1000-50 ft): \"deviation\" says \"plus ten\" / \"minus five\" when the\n"
                      "# band changes (silent when on speed); \"warnings\" says \"speed high\" / \"speed low\"",
    "speed_low_margin_kt": "# Warnings mode: \"speed low\" below Vref - margin, \"speed high\" above Vref + margin (0 = off)",
    "sink_rate_fpm": "\n# Warnings. 0 = off",
    "overspeed_warnings": "# \"flap speed\" / \"gear speed\" when the sim says the aircraft's flap or gear limit is exceeded",
    "descent_calls": "\n# Descent planning, with a destination set in the main window: \"top of descent\" when it's time to\n"
                     "# start down, \"high on profile\" if you're 1000 ft or more above the descent path. The path:\n"
                     "# descent_rate_fpm at your groundspeed, reaching descent_pattern_ft above the field\n"
                     "# descent_margin_nm before it. DC-6 mode flies its own profile instead: 1000 fpm to a level-off\n"
                     "# 3000 ft above the field about 30 nm out, with \"five hundred to level off\"",
    "landing_target_fpm": "\n# Touchdown. Default max touchdown rate in fpm (0 = off; set per flight in the main window),\n"
                          "# and whether to say the result (\"one eight two, within target\")",
    "announce_touchdown": "# Say the touchdown rate even without a target",
    "voice": "\n# Voice. \"\" = Joe, the built-in natural voice; \"windows\" = the Windows default voice; or part of an\n"
             "# installed Windows voice name, e.g. \"Zira\". Rate -10..10, volume 0..100",
    "sounds_dir": "# Optional <key>.wav overrides, e.g. 50.wav, retard.wav",
    "record_flights": "\n# Record each sim session to the flights folder (newest 20 kept), for finding bugs",
    "agl_offset_ft": "\n# Sim. Offset if an aircraft doesn't read ~0 ft on the ground; throttle SimVar for retard repeat (\"\" = off)",
}


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return repr(v)


def dump(cfg: Config) -> str:
    """The config as commented TOML."""
    lines = ["# Old Bird Copilot settings. Edit here or in the app's Settings window.", ""]
    for key in _SCHEMA:
        if key == "texts":
            continue
        if key in _COMMENTS:
            lines.append(_COMMENTS[key])
        lines.append(f"{key} = {_toml_value(getattr(cfg, key))}")
    lines += [
        "",
        "# Change what gets said. Keys: height numbers (\"50\"), rollout_<kt> (\"rollout_60\"), or",
        "# retard, minimums, approaching_minimums, speed_low, speed_high, sink_rate, too_low_gear, touchdown",
        "[texts]",
    ]
    lines += [f"{json.dumps(k)} = {_toml_value(v)}" for k, v in cfg.texts.items()]
    return "\n".join(lines) + "\n"


def save(cfg: Config, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dump(cfg), encoding="utf-8")
    tmp.replace(path)


def with_overrides(cfg: Config, **overrides) -> Config:
    """Return a copy with the given (validated) settings changed; None values are skipped."""
    changes = {}
    for key, value in overrides.items():
        if value is None:
            continue
        kind, lo, hi = _SCHEMA[key]
        changes[key] = _check(key, value, kind, lo, hi)
    return dataclasses.replace(cfg, **changes)
