"""Desktop window: live readouts, per-flight Vref/minimums, landing log and a Settings dialog."""

from __future__ import annotations

import dataclasses
import os
import queue
import sys
import threading
import time
from pathlib import Path


def _fix_tcl_in_venv() -> None:
    """Python 3.13.0 on Windows can't find Tcl/Tk from inside a venv. Point it at the base install."""
    tcl = Path(sys.base_prefix) / "tcl"
    if sys.prefix != sys.base_prefix and "TCL_LIBRARY" not in os.environ and (tcl / "tcl8.6").is_dir():
        os.environ["TCL_LIBRARY"] = str(tcl / "tcl8.6")
        os.environ.setdefault("TK_LIBRARY", str(tcl / "tk8.6"))


_fix_tcl_in_venv()

import tkinter as tk  # noqa: E402 - must come after the Tcl fix
from tkinter import font as tkfont  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

from . import APP_NAME, APP_TITLE, __version__
from . import config as config_mod
from .app import CONNECTED, PAUSED, WAITING, Controller, countdown_sample
from .airports import distance_nm
from .config import Config
from .descent import RATE_RANGE, descent_distance
from .engine import Callout, Touchdown

C = {
    "bg": "#14181d",
    "panel": "#1d2329",
    "field": "#262d35",
    "border": "#313a44",
    "text": "#e7eaee",
    "muted": "#8d97a3",
    "accent": "#5aa9ff",
    "green": "#3ecf8e",
    "amber": "#f5b942",
    "red": "#ff6b6b",
}
FONT = "Segoe UI"
BIG = (FONT, 18, "bold")   # readouts and the Takeoff / Descent / Landing numbers
POLL_MS = 100
STANDARD_HEIGHTS = [2500, 2000, 1500, 1000, 500, 400, 300, 200, 100, 50, 40, 30, 20, 10]


def touchdown_rating(fpm: float) -> tuple[str, str]:
    """(label, color) for a touchdown rate."""
    rate = abs(fpm)
    if rate < 60:
        return "Butter", C["green"]
    if rate < 180:
        return "Smooth", C["green"]
    if rate < 300:
        return "Firm", C["amber"]
    if rate < 600:
        return "Hard", C["red"]
    return "Very hard", C["red"]


# ------------------------------------------------------------------ styling

def apply_theme(root: tk.Tk) -> None:
    root.configure(bg=C["bg"])
    # No global "*Font" option: it would override the per-style fonts below.
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.background", C["field"])
    root.option_add("*TCombobox*Listbox.foreground", C["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", C["bg"])

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=C["bg"], foreground=C["text"], fieldbackground=C["field"],
                bordercolor=C["border"], lightcolor=C["border"], darkcolor=C["border"],
                troughcolor=C["field"], focuscolor=C["accent"], font=(FONT, 10))
    s.configure("TFrame", background=C["bg"])
    s.configure("Panel.TFrame", background=C["panel"])
    s.configure("TLabel", background=C["bg"], foreground=C["text"])
    s.configure("Panel.TLabel", background=C["panel"])
    s.configure("Muted.TLabel", foreground=C["muted"])
    s.configure("PanelMuted.TLabel", background=C["panel"], foreground=C["muted"], font=(FONT, 9))
    s.configure("Caption.TLabel", background=C["panel"], foreground=C["muted"], font=(FONT, 8, "bold"))
    s.configure("Readout.TLabel", background=C["panel"], foreground=C["text"], font=BIG)
    s.configure("CardTitle.TLabel", background=C["panel"], font=(FONT, 10, "bold"))
    s.configure("DescentValue.TLabel", background=C["panel"], foreground=C["text"], font=(FONT, 15, "bold"))
    s.configure("Title.TLabel", font=(FONT, 13, "bold"))
    s.configure("Section.TLabel", background=C["panel"], font=(FONT, 10, "bold"))
    s.configure("Callout.TLabel", background=C["panel"], foreground=C["accent"], font=(FONT, 14, "bold"))

    s.configure("TButton", background=C["field"], foreground=C["text"], padding=(12, 6), relief="flat")
    s.map("TButton", background=[("active", C["border"]), ("disabled", C["panel"])],
          foreground=[("disabled", C["muted"])])
    s.configure("Accent.TButton", background=C["accent"], foreground=C["bg"], font=(FONT, 10, "bold"))
    s.map("Accent.TButton", background=[("active", "#7dbcff")])
    s.configure("Small.TButton", padding=(8, 2), font=(FONT, 9))
    s.configure("Toggle.TButton", padding=(14, 6), font=(FONT, 10, "bold"))
    s.configure("On.Toggle.TButton", background="#1f4a37", foreground=C["green"])
    s.map("On.Toggle.TButton", background=[("active", "#285c45")])
    s.configure("Off.Toggle.TButton", background="#4a2a2a", foreground=C["red"])
    s.map("Off.Toggle.TButton", background=[("active", "#5c3434")])
    # Aircraft-mode switch: the chosen side is lit, the other is a dim, clickable option
    s.configure("Seg.TButton", padding=(12, 6), font=(FONT, 10, "bold"), background=C["panel"],
                foreground=C["muted"])
    s.map("Seg.TButton", background=[("active", C["field"])], foreground=[("active", C["text"])])
    s.configure("Dc6.Seg.TButton", background="#4a3d1f", foreground=C["amber"])
    s.map("Dc6.Seg.TButton", background=[("active", "#5c4c27")], foreground=[("active", C["amber"])])
    s.configure("Universal.Seg.TButton", background="#24364d", foreground=C["accent"])
    s.map("Universal.Seg.TButton", background=[("active", "#2c4260")], foreground=[("active", C["accent"])])

    for style, bg in (("TCheckbutton", C["bg"]), ("Panel.TCheckbutton", C["panel"]),
                      ("Panel.TRadiobutton", C["panel"])):
        s.configure(style, background=bg, foreground=C["text"], indicatorbackground=C["field"],
                    indicatorforeground=C["accent"], indicatormargin=(0, 0, 6, 0))
        s.map(style, background=[("active", bg)], indicatorbackground=[("selected", C["field"])])

    s.configure("TEntry", fieldbackground=C["field"], foreground=C["text"], insertcolor=C["text"], padding=4)
    s.configure("TSpinbox", fieldbackground=C["field"], foreground=C["text"], insertcolor=C["text"],
                arrowcolor=C["muted"], background=C["field"], padding=3)
    for name, color in (("Takeoff", C["green"]), ("Landing", C["accent"])):
        s.configure(f"{name}.TSpinbox", foreground=color, padding=(6, 2))
    s.configure("Descent.TEntry", foreground=C["amber"], padding=(6, 2))
    s.configure("TCombobox", fieldbackground=C["field"], foreground=C["text"], arrowcolor=C["muted"],
                background=C["field"], padding=3)
    s.map("TCombobox", fieldbackground=[("readonly", C["field"])], foreground=[("readonly", C["text"])],
          selectbackground=[("readonly", C["field"])], selectforeground=[("readonly", C["text"])])
    s.configure("Horizontal.TScale", background=C["accent"], troughcolor=C["field"])

    s.configure("TNotebook", background=C["bg"], borderwidth=0, bordercolor=C["panel"],
                lightcolor=C["panel"], darkcolor=C["panel"], tabmargins=(0, 0, 0, 0))
    s.configure("TNotebook.Tab", background=C["bg"], foreground=C["muted"], padding=(14, 6), borderwidth=0,
                bordercolor=C["bg"], lightcolor=C["bg"], focuscolor=C["panel"])
    s.map("TNotebook.Tab", background=[("selected", C["panel"])], foreground=[("selected", C["text"])],
          lightcolor=[("selected", C["panel"])], bordercolor=[("selected", C["panel"])])

    s.configure("Treeview", background=C["panel"], fieldbackground=C["panel"], foreground=C["text"],
                rowheight=24, borderwidth=0, bordercolor=C["panel"], lightcolor=C["panel"], darkcolor=C["panel"])
    s.configure("Treeview.Heading", background=C["panel"], foreground=C["muted"], relief="flat",
                font=(FONT, 9, "bold"))
    s.map("Treeview.Heading", background=[("active", C["panel"])])
    s.map("Treeview", background=[("selected", C["field"])], foreground=[("selected", C["text"])])


def make_icon(size: int = 32) -> tk.PhotoImage:
    """A blue disc with a white downward chevron (descending to land)."""
    img = tk.PhotoImage(width=size, height=size)
    c, r = (size - 1) / 2, size / 2
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            dx, dy = x - c, y - c
            if dx * dx + dy * dy > r * r:
                row.append("")  # transparent
                continue
            # chevron: two thick strokes meeting at the bottom center
            u, v = abs(dx) / r, (y - size * 0.28) / r
            on_chevron = 0 <= v <= 0.85 and abs(v - (0.85 - u * 0.95) + 0.15) < 0.2 and u < 0.62
            row.append("#ffffff" if on_chevron else C["accent"])
        rows.append(row)
    for y, row in enumerate(rows):
        for x, color in enumerate(row):
            if color:
                img.put(color, (x, y))
    return img


def _show(lbl, btn, text: str, color: str, btn_text: str) -> None:
    """A suggestion line with its Use button: shown with text, hidden without."""
    if lbl.cget("text") == text:
        return
    lbl.configure(text=text, foreground=color)
    btn.configure(text=btn_text)
    if text:
        lbl.grid()
        btn.grid()
    else:
        lbl.grid_remove()
        btn.grid_remove()


def panel(parent, **grid) -> ttk.Frame:
    f = ttk.Frame(parent, style="Panel.TFrame", padding=12)
    f.grid(**grid)
    return f


# --------------------------------------------------------------- main window

class MainWindow:
    def __init__(self, root: tk.Tk, ctl: Controller):
        self.root = root
        self.ctl = ctl
        self._last_status_key = None
        self._flight_after = None

        root.title(APP_TITLE)
        self._icon = make_icon()
        root.iconphoto(True, self._icon)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.columnconfigure(0, weight=1)

        outer = ttk.Frame(root, padding=16)
        outer.grid(sticky="nsew")
        root.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        self._build_header(outer)
        self._build_readouts(outer)
        self._build_flight(outer)
        self._build_callout(outer)
        self._build_tabs(outer)
        self._build_buttons(outer)
        outer.rowconfigure(4, weight=1)

        self._refresh_status()
        self._refresh_dc6()      # the Aircraft switch's bold style is part of the width
        self._size_window()
        root.after(POLL_MS, self.poll)

    @property
    def _min_tabs_px(self) -> int:
        """The Landings/Activity tabs squeezed to the tab strip, the column titles and a row."""
        return 3 * (tkfont.nametofont("TkDefaultFont").metrics("linespace") + 12)

    def _worst_case_size(self) -> tuple[int, int, int]:
        """(width, full height, smallest height) with the widest/tallest content the window will show:
        DC-6 suggestions, a long airport, descent numbers. Filled in for a moment, measured, cleared."""
        sample = [(self.suggest_lbl, "Suggested Vref (88,000 lb):  111 kt, full flaps  (over max landing wt)"),
                  (self.to_suggest_lbl, "Suggested (wet, 104,000 lb):  V1 104  ·  VR 113  ·  V2 118  (over max wt)"),
                  (self.dest_lbl, "Ted Stevens Anchorage International Airport, elevation 152 ft.   Plan: 1,000 fpm, "
                                  "level off at 3,152 ft 30 nm out."),
                  (self.status_lbl, "Sim connection off"),
                  (self.status_sub, "Start the sim and load a flight. It connects by itself.")]
        sample += [(v, t) for v, t in zip(self.dest_vals.values(), ("888 nm", "passed", "12,300 ft", "2,200 high"))]
        sample += [(v, t) for v, t in zip(self.dest_subs.values(), ("", "88 nm ago", "you 14,500 ft", "needs 1,250 fpm"))]
        hidden = [w for w in (self.suggest_lbl, self.suggest_btn, self.to_suggest_lbl, self.to_suggest_btn)
                  if not w.winfo_manager()]
        before = [(w, w.cget("text")) for w, _ in sample]
        self.root.update_idletasks()
        real_width = self.root.winfo_reqwidth()      # wrapped text can come out narrower with the samples
        for w in hidden:
            w.grid()
        for w, text in sample:
            w.configure(text=text)
        self.root.update_idletasks()
        width, height = max(real_width, self.root.winfo_reqwidth()), self.root.winfo_reqheight()
        squeezable = max(0, self.nb.winfo_reqheight() - self._min_tabs_px)
        for w, text in before:
            w.configure(text=text)
        for w in hidden:
            w.grid_remove()
        self.root.update_idletasks()
        return width, height, height - squeezable

    def needed_height(self) -> int:
        """Smallest window height that shows everything (Landings list squeezed to a row)."""
        return self._worst_case_size()[2]

    def _size_window(self):
        """Size the window for its widest content (it grows with the text scaling), never taller than the
        screen. The Landings list (the only stretchy part) absorbs any shortage."""
        root = self.root
        width, height, smallest = self._worst_case_size()
        scale = windows_scaling_pct(root) / 100
        usable = int(screen_work_height(root) - TITLE_BAR_PX * scale)
        root.minsize(width, min(smallest, usable))
        root.geometry(f"{width + int(8 * scale)}x{min(height, usable)}")

    # -- layout

    def _build_header(self, parent):
        f = ttk.Frame(parent)
        f.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        f.columnconfigure(1, weight=1)
        self.dot = tk.Canvas(f, width=14, height=14, bg=C["bg"], highlightthickness=0)
        self.dot_item = self.dot.create_oval(2, 2, 13, 13, fill=C["amber"], outline="")
        self.dot.grid(row=0, column=0, rowspan=2, padx=(0, 10))
        self.status_lbl = ttk.Label(f, text="", style="Title.TLabel")
        self.status_lbl.grid(row=0, column=1, sticky="w")
        self.status_sub = ttk.Label(f, text="", style="Muted.TLabel", wraplength=270, justify="left")
        self.status_sub.grid(row=1, column=1, sticky="w")
        self.mute_btn = ttk.Button(f, command=self.toggle_mute)
        self.mute_btn.grid(row=0, column=3, rowspan=2, sticky="e")
        # Aircraft mode as a two-way switch, so it reads as a choice: Universal (any aircraft) or DC-6 (PMDG
        # tables and cues). Picks itself from the loaded aircraft; click either side to choose.
        mode = ttk.Frame(f)
        mode.grid(row=0, column=2, rowspan=2, sticky="e", padx=(0, 14))
        ttk.Label(mode, text="Aircraft:", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 6))
        self.mode_btns = {}
        for col, (dc6, text) in enumerate(((False, "UNIVERSAL"), (True, "DC-6")), 1):
            b = ttk.Button(mode, text=text, cursor="hand2", command=lambda dc6=dc6: self._pick_mode(dc6))
            b.grid(row=0, column=col)
            self.mode_btns[dc6] = b
        self._refresh_mute()

    def _build_readouts(self, parent):
        f = panel(parent, row=1, column=0, sticky="ew", pady=(0, 10))
        self.readouts = {}
        for i, (key, caption) in enumerate([("agl", "HEIGHT"), ("ias", "SPEED"), ("vs", "V/S (FPM)"),
                                            ("gear", "GEAR"), ("flaps", "FLAPS"), ("thr", "THROTTLE")]):
            f.columnconfigure(i, weight=1, uniform="r")
            ttk.Label(f, text=caption, style="Caption.TLabel").grid(row=0, column=i, sticky="w")
            lbl = ttk.Label(f, text="—", style="Readout.TLabel")
            lbl.grid(row=1, column=i, sticky="w")
            self.readouts[key] = lbl
        self.armed_lbl = ttk.Label(f, text="", style="PanelMuted.TLabel")
        self.armed_lbl.grid(row=2, column=0, columnspan=6, sticky="w", pady=(6, 0))
        self.weight_lbl = ttk.Label(f, text="", style="PanelMuted.TLabel")
        self.weight_lbl.grid(row=3, column=0, columnspan=6, sticky="w", pady=(2, 0))

    def _build_flight(self, parent):
        """Three panels in flight order: Takeoff, Descent, Landing. Big colored numbers like the readouts."""
        box = ttk.Frame(parent)
        box.grid(row=2, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)

        def card(row, title, color):
            f = panel(box, row=row, column=0, sticky="ew", pady=(0, 6))
            f.columnconfigure(9, weight=1)
            ttk.Label(f, text=title, style="CardTitle.TLabel", foreground=color, width=10).grid(
                row=0, column=0, rowspan=2, sticky="w")
            return f

        def field(f, col, caption, var, unit, hi, step, style):
            ttk.Label(f, text=caption, style="Caption.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 22))
            cell = ttk.Frame(f, style="Panel.TFrame")
            cell.grid(row=1, column=col, sticky="w", padx=(0, 22))
            sp = ttk.Spinbox(cell, from_=0, to=hi, increment=step, width=5, textvariable=var, font=BIG,
                             style=style, command=self._flight_changed)
            sp.grid(row=0, column=0)
            sp.bind("<KeyRelease>", lambda _e: self._flight_changed())
            ttk.Label(cell, text=unit, style="PanelMuted.TLabel").grid(row=0, column=1, sticky="s", padx=(5, 0),
                                                                       pady=(0, 6))

        def suggestion(f, row, command):
            lbl = ttk.Label(f, text="", style="Panel.TLabel")
            lbl.grid(row=row, column=1, columnspan=8, sticky="w", pady=(8, 0))
            btn = ttk.Button(f, text="Use", command=command)
            btn.grid(row=row, column=9, sticky="e", pady=(8, 0))
            lbl.grid_remove()
            btn.grid_remove()
            return lbl, btn

        # Takeoff
        t = card(0, "TAKEOFF", C["green"])
        self.v1_var = tk.StringVar(value=str(self.ctl.flight_v1))
        self.vr_var = tk.StringVar(value=str(self.ctl.flight_vr))
        self.v2_var = tk.StringVar(value=str(self.ctl.flight_v2))
        for col, (caption, var) in enumerate((("V1", self.v1_var), ("VR", self.vr_var), ("V2", self.v2_var)), 1):
            field(t, col, caption, var, "kt", 250, 1, "Takeoff.TSpinbox")
        self.to_suggest_lbl, self.to_suggest_btn = suggestion(t, 2, self._use_takeoff_suggestion)

        # Descent
        d = card(1, "DESCENT", C["amber"])
        ttk.Label(d, text="DESTINATION", style="Caption.TLabel").grid(row=0, column=1, sticky="w", padx=(0, 22))
        self.dest_var = tk.StringVar()
        dest = ttk.Entry(d, textvariable=self.dest_var, width=6, font=BIG, style="Descent.TEntry")
        dest.grid(row=1, column=1, sticky="w", padx=(0, 22))
        dest.bind("<KeyRelease>", lambda _e: self._dest_changed())
        dest.bind("<Return>", lambda _e: self._apply_destination())
        # Where you are against the plan: big value, small line under it
        self.dest_vals, self.dest_subs = {}, {}
        for col, (key, caption) in enumerate((("distance", "DISTANCE"), ("start", "START DOWN"),
                                              ("path", "SHOULD BE AT"), ("diff", "DIFFERENCE")), 2):
            ttk.Label(d, text=caption, style="Caption.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 18))
            v = ttk.Label(d, text="—", style="DescentValue.TLabel")
            v.grid(row=1, column=col, sticky="w", padx=(0, 18))
            sub = ttk.Label(d, text="", style="PanelMuted.TLabel")
            sub.grid(row=2, column=col, sticky="nw", padx=(0, 18))
            self.dest_vals[key], self.dest_subs[key] = v, sub
        self.dest_lbl = ttk.Label(d, text="", style="PanelMuted.TLabel", wraplength=640, justify="left")
        self.dest_lbl.grid(row=3, column=0, columnspan=10, sticky="w", pady=(6, 0))
        ttk.Button(d, text="Calculator…", style="Small.TButton", command=self.open_calculator).grid(
            row=2, column=1, sticky="nw", pady=(4, 0))
        self._calc = None
        self._dest_after = None
        self._dest_error = ""

        # Landing
        lnd = card(2, "LANDING", C["accent"])
        self.vref_var = tk.StringVar(value=str(self.ctl.flight_vref))
        self.mins_var = tk.StringVar(value=str(self.ctl.flight_minimums))
        self.target_var = tk.StringVar(value=str(self.ctl.flight_landing_target))
        for col, (caption, var, unit, hi, step) in enumerate((
                ("VREF", self.vref_var, "kt", 250, 1),
                ("MINIMUMS", self.mins_var, "ft", 5000, 10),
                ("MAX LANDING", self.target_var, "fpm", 1000, 10)), 1):
            field(lnd, col, caption, var, unit, hi, step, "Landing.TSpinbox")
        self.suggest_lbl, self.suggest_btn = suggestion(lnd, 2, self._use_suggestion)
        self.flight_hint = ttk.Label(lnd, text="", style="PanelMuted.TLabel", wraplength=640, justify="left")
        self.flight_hint.grid(row=3, column=0, columnspan=10, sticky="w", pady=(6, 0))

        # Options under the panels
        opts = ttk.Frame(box)
        opts.grid(row=3, column=0, sticky="w", pady=(0, 8))
        self.heights_var = tk.BooleanVar(value=self.ctl.cfg.height_callouts)
        ttk.Checkbutton(opts, text="Height callouts", variable=self.heights_var,
                        command=self._heights_changed).grid(row=0, column=0, sticky="w", padx=(0, 18))
        self.dc6_lbl = ttk.Label(opts, text="", style="Muted.TLabel")
        self.dc6_lbl.grid(row=0, column=1, sticky="w")
        self._update_flight_hint()

    def _refresh_dc6(self):
        active = self.ctl.dc6_active
        for dc6, b in self.mode_btns.items():
            style = ("Dc6.Seg.TButton" if dc6 else "Universal.Seg.TButton") if dc6 == active else "Seg.TButton"
            if b.cget("style") != style:
                b.configure(style=style)
        if active:
            text = "PMDG speeds, flaps checks, 40 kt cue, DC-6 descent profile"
            text += "  (switched on by you)" if self.ctl.dc6_override is not None else "  (DC-6 detected)"
        elif self.ctl.dc6_override is not None:
            text = "Universal: any aircraft  (DC-6 mode switched off by you)"
        else:
            text = "Universal: any aircraft. Switches to DC-6 mode when the DC-6 is loaded"
        if self.dc6_lbl.cget("text") != text:
            self.dc6_lbl.configure(text=text)

    def _pick_mode(self, dc6: bool):
        if dc6 != self.ctl.dc6_active:
            self.ctl.set_dc6_mode(dc6)
        self._refresh_dc6()

    def _use_takeoff_suggestion(self):
        t = self.ctl.takeoff_suggestion
        if t is not None:
            self.v1_var.set(str(t.v1))
            self.vr_var.set(str(t.vr))
            self.v2_var.set(str(t.v2))
            self._apply_flight()
            self.log(f"Take-off speeds set: V1 {t.v1}, VR {t.vr}, V2 {t.v2} "
                     f"(DC-6 table, {t.weight_lb:,.0f} lb, {'wet' if t.wet else 'dry'}).")

    def _build_callout(self, parent):
        f = panel(parent, row=3, column=0, sticky="ew", pady=(0, 10))
        f.columnconfigure(0, weight=1)
        ttk.Label(f, text="LAST CALLOUT", style="Caption.TLabel").grid(row=0, column=0, sticky="w")
        self.callout_lbl = ttk.Label(f, text="—", style="Callout.TLabel")
        self.callout_lbl.grid(row=1, column=0, sticky="w")

    def _build_tabs(self, parent):
        nb = self.nb = ttk.Notebook(parent)
        nb.grid(row=4, column=0, sticky="nsew", pady=(0, 12))

        landings = ttk.Frame(nb, style="Panel.TFrame", padding=(8, 8))
        landings.columnconfigure(0, weight=1)
        landings.rowconfigure(0, weight=1)
        cols = ("time", "vs", "ias", "rating")
        self.landings = ttk.Treeview(landings, columns=cols, show="headings", height=2, selectmode="none")
        for col, title, w in zip(cols, ("Time", "Touchdown rate", "Speed", "Rating"), (70, 130, 80, 220)):
            self.landings.heading(col, text=title, anchor="w")
            self.landings.column(col, width=w, anchor="w")
        for label, color in (("Butter", C["green"]), ("Smooth", C["green"]), ("Firm", C["amber"]),
                             ("Hard", C["red"]), ("Very hard", C["red"])):
            self.landings.tag_configure(label, foreground=color)
        self.landings.tag_configure("target_ok", foreground=C["green"])
        self.landings.tag_configure("target_missed", foreground=C["red"])
        self.landings.grid(row=0, column=0, sticky="nsew")
        self.no_landings = ttk.Label(landings, text="Your landings will show up here.", style="PanelMuted.TLabel")
        self.no_landings.place(relx=0.5, rely=0.55, anchor="center")
        nb.add(landings, text="Landings")

        activity = ttk.Frame(nb, style="Panel.TFrame", padding=(8, 8))
        activity.columnconfigure(0, weight=1)
        activity.rowconfigure(0, weight=1)
        self.activity = tk.Listbox(activity, bg=C["panel"], fg=C["text"], borderwidth=0, highlightthickness=0,
                                   selectbackground=C["field"], activestyle="none", font=("Consolas", 9), height=4)
        self.activity.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(activity, orient="vertical", command=self.activity.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.activity.configure(yscrollcommand=sb.set)
        nb.add(activity, text="Activity")

    def _build_buttons(self, parent):
        f = ttk.Frame(parent)
        f.grid(row=5, column=0, sticky="ew")
        f.columnconfigure(4, weight=1)
        ttk.Button(f, text="Test voice", command=self.ctl.test_voice).grid(row=0, column=0, padx=(0, 8))
        self.demo_btn = ttk.Button(f, text="Demo approach", command=self.toggle_demo)
        self.demo_btn.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(f, text="Settings…", command=self.open_settings).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(f, text="⚑ Flag moment", command=self.ctl.flag_moment).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(f, text="About", style="Small.TButton", command=lambda: AboutDialog(self)).grid(row=0, column=4,
                                                                                                    sticky="w")
        self.topmost_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Stay on top", variable=self.topmost_var,
                        command=lambda: self.root.attributes("-topmost", self.topmost_var.get())
                        ).grid(row=0, column=5, sticky="e")

    # -- actions

    def toggle_mute(self):
        self.ctl.muted = not self.ctl.muted
        self.log("Callouts muted." if self.ctl.muted else "Callouts on.")
        self._refresh_mute()
        self._refresh_status(force=True)

    def _refresh_mute(self):
        if self.ctl.muted:
            self.mute_btn.configure(text="Callouts OFF", style="Off.Toggle.TButton")
        else:
            self.mute_btn.configure(text="Callouts ON", style="On.Toggle.TButton")

    def toggle_demo(self):
        if self.ctl.demo_running:
            self.ctl.stop_demo()
        else:
            self.ctl.start_demo()
            self.demo_btn.configure(text="Stop demo")
            self._refresh_status(force=True)

    def open_settings(self):
        SettingsDialog(self)

    def open_calculator(self):
        if self._calc is not None and self._calc.win.winfo_exists():
            self._calc.win.lift()
        else:
            self._calc = DescentCalculator(self)

    def _refresh_suggestion(self):
        self._refresh_dc6()
        s, t = self.ctl.suggestion, self.ctl.takeoff_suggestion
        text = ""
        if s is not None:
            text = f"Suggested Vref ({s.weight_lb:,.0f} lb):  {s.full} kt, full flaps" + (f"  ({s.note})" if s.note else "")
        _show(self.suggest_lbl, self.suggest_btn, text, C["amber"] if s and s.note else C["text"],
              f"Use {s.full}" if s else "Use")
        to_text = ""
        if t is not None:
            to_text = (f"Suggested ({'wet' if t.wet else 'dry'}, {t.weight_lb:,.0f} lb):  V1 {t.v1}  ·  VR {t.vr}"
                       f"  ·  V2 {t.v2}" + (f"  ({t.note})" if t.note else ""))
        _show(self.to_suggest_lbl, self.to_suggest_btn, to_text, C["amber"] if t and t.note else C["text"], "Use")

    def _use_suggestion(self):
        s = self.ctl.suggestion
        if s is not None:
            self.vref_var.set(str(s.full))
            self._apply_flight()
            self.log(f"Vref set to {s.full} kt (DC-6 table, {s.weight_lb:,.0f} lb, flaps full).")

    def _dest_changed(self):
        # Debounce typing, then look the airport up.
        if self._dest_after:
            self.root.after_cancel(self._dest_after)
        self._dest_after = self.root.after(1200, self._apply_destination)   # PAS... is an airport too: wait for PASY

    def _apply_destination(self):
        self._dest_after = None
        code = self.dest_var.get().strip().upper()
        found = self.ctl.set_destination(code)
        self._dest_error = f"{code} not found in the airport list" if code and found is None else ""
        self._refresh_destination()

    def _refresh_destination(self):
        from .descent import Readouts, readouts
        ctl, ap, s = self.ctl, self.ctl.descent.destination, self.ctl.latest
        ctl.sync_descent()
        if self._dest_error:
            text, color = self._dest_error, C["red"]
        elif ap is None:
            text, color = "Type an airport code (e.g. PASN) to see when to start down and where you should be.", \
                C["muted"]
        else:
            p = ctl.descent
            text = (f"{ap.name}, elevation {ap.elev_ft:,} ft.   Plan: {ctl.descent_rate:,} fpm, "
                    + ("level off" if p.dc6 else "be") + f" at {ap.elev_ft + p.arrive_agl_ft:,} ft {p.margin_nm} nm out.")
            color = C["muted"]
        if self.dest_lbl.cget("text") != text:
            self.dest_lbl.configure(text=text, foreground=color)
        r = (readouts(ctl.descent.info, s.altitude_ft if s else None, s.gs_kt if s else None, ctl.descent.margin_nm)
             if ap is not None else Readouts())
        alert = C["amber"] if r.alert else C["text"]
        for key, value, sub, color in (("distance", r.distance, "", C["text"]),
                                       ("start", r.start_down, r.start_sub, alert if r.start_down == "NOW" else C["text"]),
                                       ("path", r.path, r.path_sub, C["text"]),
                                       ("diff", r.diff, r.diff_sub,
                                        C["green"] if r.diff == "on path" else alert)):
            if self.dest_vals[key].cget("text") != value or str(self.dest_vals[key].cget("foreground")) != color:
                self.dest_vals[key].configure(text=value, foreground=color)
            if self.dest_subs[key].cget("text") != sub:
                self.dest_subs[key].configure(text=sub)

    def _heights_changed(self):
        self.ctl.set_height_callouts(self.heights_var.get())
        self._update_flight_hint()

    def _flight_changed(self):
        # Debounce typing, then apply.
        if self._flight_after:
            self.root.after_cancel(self._flight_after)
        self._flight_after = self.root.after(400, self._apply_flight)

    def _apply_flight(self):
        self._flight_after = None
        try:
            vref = int(self.vref_var.get() or 0)
            mins = int(self.mins_var.get() or 0)
            target = int(self.target_var.get() or 0)
            v1, vr, v2 = (int(v.get() or 0) for v in (self.v1_var, self.vr_var, self.v2_var))
        except ValueError:
            self.flight_hint.configure(text="All the boxes must be whole numbers (or empty).", foreground=C["red"])
            return
        vref = max(0, min(vref, 400))
        mins = max(0, min(mins, 50_000))
        target = max(0, min(abs(target), 2000))   # typing -200 means the same as 200
        self.ctl.set_flight(vref, mins, target)
        self.ctl.set_takeoff(*(max(0, min(v, 300)) for v in (v1, vr, v2)))
        self._update_flight_hint()

    def _update_flight_hint(self):
        parts = []
        v, m = self.ctl.flight_vref, self.ctl.flight_minimums
        cfg = self.ctl.cfg
        if v and cfg.speed_callouts == "deviation":
            lo, hi = self._speed_band()
            parts.append(f"On speed: {lo}–{hi} kt. Outside that you'll hear \"plus ten\", \"minus five\"...")
        elif v:
            lo = v - cfg.speed_low_margin_kt
            hi = f", \"speed high\" above {v + cfg.speed_high_margin_kt} kt" if cfg.speed_high_margin_kt else ""
            parts.append(f"\"Speed low\" below {lo} kt{hi}.")
        else:
            parts.append("Set Vref for speed warnings.")
        parts.append(f"\"Minimums\" at {m} ft." if m else "Minimums off.")
        t = self.ctl.flight_landing_target
        if t:
            parts.append(f"Landing target: {t} fpm or softer.")
        if not cfg.height_callouts:
            parts.append("Height callouts off.")
        self.flight_hint.configure(text="  ".join(parts), foreground=C["muted"])

    def _off_speed(self, ias: float) -> bool:
        """True where a speed callout would fire (matches engine.py)."""
        v, cfg = self.ctl.flight_vref, self.ctl.cfg
        if cfg.speed_callouts == "deviation":
            d = ias - v
            return bool((cfg.speed_plus_kt and d >= min(cfg.speed_plus_kt))
                        or (cfg.speed_minus_kt and d <= -min(cfg.speed_minus_kt)))
        return ias < v - cfg.speed_low_margin_kt or bool(
            cfg.speed_high_margin_kt and ias > v + cfg.speed_high_margin_kt)

    def _speed_band(self) -> tuple[int, int]:
        """The whole-knot on-speed range (no speed callout) for the current Vref, for display."""
        v, cfg = self.ctl.flight_vref, self.ctl.cfg
        if cfg.speed_callouts == "deviation":
            lo = v - min(cfg.speed_minus_kt) + 1 if cfg.speed_minus_kt else 0
            hi = v + min(cfg.speed_plus_kt) - 1 if cfg.speed_plus_kt else 999
            return lo, hi
        return (v - cfg.speed_low_margin_kt,
                v + cfg.speed_high_margin_kt if cfg.speed_high_margin_kt else 999)

    def set_flight_fields(self, vref: int, mins: int):
        self.vref_var.set(str(vref))
        self.mins_var.set(str(mins))
        self._apply_flight()

    def log(self, msg: str):
        self.activity.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}")
        if self.activity.size() > 500:
            self.activity.delete(0, self.activity.size() - 500)
        self.activity.see("end")

    def add_landing(self, td: Touchdown):
        label, _color = touchdown_rating(td.vs_fpm)
        tag = label
        if td.target_fpm:
            ok = td.within_target
            label = f"{label}  {'✓' if ok else '✗'} {td.target_fpm} target"
            tag = "target_ok" if ok else "target_missed"
        self.no_landings.place_forget()
        self.landings.insert("", 0, values=(time.strftime("%H:%M"), f"{td.vs_fpm:+.0f} fpm",
                                            f"{td.ias_kt:.0f} kt", label), tags=(tag,))

    def on_close(self):
        self.root.withdraw()
        self.ctl.shutdown()
        self.root.destroy()

    # -- polling

    def poll(self):
        try:
            while True:
                kind, payload = self.ctl.events.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "callout":
                    self.callout_lbl.configure(text=f"“{payload}”")
                    self.log(f"» {payload}")
                elif kind == "touchdown":
                    self.add_landing(payload)
                elif kind == "demo_done":
                    self.demo_btn.configure(text="Demo approach")
        except queue.Empty:
            pass
        self._refresh_readouts()
        self._refresh_status()
        self._refresh_suggestion()
        self._refresh_destination()
        self.root.after(POLL_MS, self.poll)

    def _refresh_readouts(self):
        s = self.ctl.latest
        r = self.readouts
        if s is None:
            for lbl in r.values():
                lbl.configure(text="—", foreground=C["text"])
            self.armed_lbl.configure(text="")
            self.weight_lbl.configure(text="")
            return
        weight = []
        if s.weight_lb is not None:
            weight.append(f"Weight {s.weight_lb:,.0f} lb")
        if s.fuel_lb is not None:
            weight.append(f"fuel {s.fuel_lb:,.0f} lb")
        self.weight_lbl.configure(text="  ·  ".join(weight))
        r["agl"].configure(text=f"{s.agl_ft:,.0f} ft")
        off_speed = self.ctl.speed_watch and self._off_speed(s.ias_kt)
        overspeed = s.flap_overspeed or s.gear_overspeed
        r["ias"].configure(text=f"{s.ias_kt:.0f} kt",
                           foreground=C["red"] if overspeed else C["amber"] if off_speed else C["text"])
        r["vs"].configure(text=f"{s.vs_fpm:+,.0f}", foreground=C["amber"] if s.vs_fpm < -1000 else C["text"])
        r["gear"].configure(text="DOWN" if s.gear_down else "UP",
                            foreground=C["green"] if s.gear_down else C["amber"])
        r["flaps"].configure(text=f"{s.flaps_deg:.0f}°" if s.flaps_deg is not None else "n/a")
        r["thr"].configure(text=f"{s.throttle_pct:.0f}%" if s.throttle_pct is not None else "n/a")
        if s.on_ground:
            self.armed_lbl.configure(text="On the ground")
        elif self.ctl.armed:
            self.armed_lbl.configure(text="Next callouts: " + ", ".join(str(h) for h in self.ctl.armed if h < s.agl_ft))
        else:
            self.armed_lbl.configure(text="")

    def _refresh_status(self, force: bool = False):
        ctl = self.ctl
        key = (ctl.status, ctl.demo_running, ctl.muted)
        if key == self._last_status_key and not force:
            return
        self._last_status_key = key
        if ctl.demo_running:
            color, title, sub = C["accent"], "Demo approach", "A fake approach is playing. Nothing is sent to the sim."
        elif ctl.status == CONNECTED:
            color, title = C["green"], "Connected to MSFS"
            sub = "Callouts are muted." if ctl.muted else "Callouts are active. Have a good flight."
        elif ctl.status == PAUSED:
            color, title, sub = C["amber"], "Sim paused", "Callouts resume when you unpause."
        elif ctl.status == WAITING:
            color, title, sub = C["amber"], "Waiting for MSFS", "Start the sim and load a flight. It connects by itself."
        else:  # NO_SIM
            color, title, sub = C["muted"], "Sim connection off", "Started with --no-sim. Demo and voice test still work."
        self.dot.itemconfigure(self.dot_item, fill=color)
        self.status_lbl.configure(text=title)
        self.status_sub.configure(text=sub)


# ---------------------------------------------------------- descent calculator

class DescentCalculator:
    """When to start down: from / to altitude and groundspeed (live or typed), descent rate on a slider.

    The slider is also the rate behind the spoken "top of descent". DC-6 mode recommends 1000 fpm.
    """

    def __init__(self, main: MainWindow):
        self.main, self.ctl = main, main.ctl
        self.win = win = tk.Toplevel(main.root)
        win.title("Descent calculator")
        win.configure(bg=C["bg"])
        win.transient(main.root)
        win.resizable(False, False)
        win.iconphoto(False, main._icon)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda _e: self.close())

        f = ttk.Frame(win, style="Panel.TFrame", padding=16)
        f.grid(sticky="nsew")
        s = self.ctl.latest
        live = s is not None and s.altitude_ft is not None
        self.from_var = tk.StringVar(value=str(round(s.altitude_ft, -2)) if live else "15000")
        self.to_var = tk.StringVar(value="1500")
        self.gs_var = tk.StringVar(value=str(round(s.gs_kt)) if live and s.gs_kt else "240")
        self.from_live = tk.BooleanVar(value=live)
        self.gs_live = tk.BooleanVar(value=live)
        self.to_dest = tk.BooleanVar(value=self.ctl.descent.destination is not None)

        def row(r, label, var, unit, check_text, check_var):
            ttk.Label(f, text=label, style="Panel.TLabel").grid(row=r, column=0, sticky="w", pady=4)
            e = ttk.Entry(f, textvariable=var, width=8)
            e.grid(row=r, column=1, sticky="w", padx=(10, 4), pady=4)
            e.bind("<KeyRelease>", lambda _e: self.refresh())
            ttk.Label(f, text=unit, style="PanelMuted.TLabel").grid(row=r, column=2, sticky="w", pady=4)
            ttk.Checkbutton(f, text=check_text, variable=check_var, style="Panel.TCheckbutton",
                            command=self.refresh).grid(row=r, column=3, sticky="w", padx=(14, 0), pady=4)
            return e

        self.from_entry = row(0, "From altitude", self.from_var, "ft", "my altitude now", self.from_live)
        self.to_entry = row(1, "Be at", self.to_var, "ft", "from destination", self.to_dest)
        self.gs_entry = row(2, "Groundspeed", self.gs_var, "kt", "my groundspeed now", self.gs_live)

        ttk.Label(f, text="Descent rate", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 4))
        self.rate_var = tk.DoubleVar(value=self.ctl.descent_rate)
        ttk.Scale(f, from_=RATE_RANGE[0], to=RATE_RANGE[1], orient="horizontal", length=200,
                  variable=self.rate_var, command=lambda _v: self._rate_moved()).grid(
            row=3, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=(10, 4))
        self.rate_lbl = ttk.Label(f, text="", style="Panel.TLabel", width=9)
        self.rate_lbl.grid(row=3, column=3, sticky="w", padx=(14, 0), pady=(10, 4))
        self.rec_btn = ttk.Button(f, text="", command=self._recommended)
        self.rec_btn.grid(row=4, column=1, columnspan=3, sticky="w", padx=(10, 0))
        self.rate_note = ttk.Label(f, text="", style="PanelMuted.TLabel", wraplength=420, justify="left")
        self.rate_note.grid(row=5, column=0, columnspan=4, sticky="w", pady=(6, 0))

        ttk.Separator(f).grid(row=6, column=0, columnspan=4, sticky="ew", pady=12)
        self.result = ttk.Label(f, text="", style="Callout.TLabel", wraplength=440, justify="left")
        self.result.grid(row=7, column=0, columnspan=4, sticky="w")
        self.detail = ttk.Label(f, text="", style="PanelMuted.TLabel", wraplength=440, justify="left")
        self.detail.grid(row=8, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.dest_line = ttk.Label(f, text="", style="Panel.TLabel", wraplength=440, justify="left")
        self.dest_line.grid(row=9, column=0, columnspan=4, sticky="w", pady=(8, 0))

        win.update_idletasks()
        x = main.root.winfo_rootx() + (main.root.winfo_width() - win.winfo_width()) // 2
        win.geometry(f"+{max(x, 0)}+{max(main.root.winfo_rooty() + 120, 0)}")
        self.refresh()
        self._tick()

    def close(self):
        self.main._calc = None
        self.win.destroy()

    def _tick(self):
        if self.win.winfo_exists():
            self.refresh()
            self.win.after(500, self._tick)

    def _rate_moved(self):
        fpm = int(round(self.rate_var.get() / 100) * 100)
        self.ctl.set_descent_rate(fpm)
        self.refresh()

    def _recommended(self):
        self.ctl.set_descent_rate(None)
        self.rate_var.set(self.ctl.descent_rate)
        self.refresh()

    def refresh(self):
        ctl, s = self.ctl, self.ctl.latest
        ap = ctl.descent.destination
        # live values and the destination fill their boxes (and lock them) while ticked
        for live, var, entry, value in (
                (self.from_live, self.from_var, self.from_entry,
                 None if s is None or s.altitude_ft is None else round(s.altitude_ft, -2)),
                (self.gs_live, self.gs_var, self.gs_entry,
                 None if s is None or s.gs_kt is None or s.gs_kt < 60 else round(s.gs_kt)),
                (self.to_dest, self.to_var, self.to_entry,
                 None if ap is None else ap.elev_ft + ctl.descent.arrive_agl_ft)):
            use = live.get() and value is not None
            if use and var.get() != str(int(value)):
                var.set(str(int(value)))
            entry.configure(state="disabled" if use else "normal")

        rate = ctl.descent_rate
        if ctl.descent_rate_override is None and int(self.rate_var.get()) != rate:
            self.rate_var.set(rate)   # follows DC-6 mode until you move the slider
        self.rate_lbl.configure(text=f"{rate:,} fpm")
        rec = ctl.recommended_descent_rate
        self.rec_btn.configure(text=f"Recommended: {rec:,} fpm" + (" (DC-6)" if ctl.dc6_active else ""))
        self.rate_note.configure(text=(
            "DC-6: 1,000 fpm at cruise power, down to 3,000 ft above the field about 30 nm out. "
            "Slide it for a gentler (500) or faster descent." if ctl.dc6_active else
            "Rough guide: piston airliners 500-1,000 fpm, turboprops 1,000-1,500, jets 1,500-2,500."))

        try:
            frm, to, gs = (float(v.get().replace(",", "")) for v in (self.from_var, self.to_var, self.gs_var))
        except ValueError:
            self.result.configure(text="Type whole numbers in the boxes.", foreground=C["red"])
            self.detail.configure(text="")
            self.dest_line.configure(text="")
            return
        if gs <= 0:
            self.result.configure(text="Groundspeed must be above 0.", foreground=C["red"])
            return
        nm, minutes = descent_distance(frm, to, gs, rate)
        if frm <= to:
            self.result.configure(text="Nothing to lose: you're already at or below that.", foreground=C["text"])
            self.detail.configure(text="")
        else:
            self.result.configure(text=f"Start down {nm:,.0f} nm before you need to be at {to:,.0f} ft",
                                  foreground=C["text"])
            self.detail.configure(text=f"{frm - to:,.0f} ft to lose at {rate:,} fpm: {minutes:,.0f} min at "
                                       f"{gs:,.0f} kt  ·  {gs / 60 / rate * 1000:.1f} nm per 1,000 ft")
        if ap is not None and s is not None and s.lat is not None and s.lon is not None:
            margin = ctl.descent.margin_nm
            dist = distance_nm(s.lat, s.lon, ap.lat, ap.lon)
            left = dist - nm - margin
            when = f"descend in {left:,.0f} nm" if left > 0.5 else "time to be descending"
            self.dest_line.configure(text=f"{ap.code} is {dist:,.0f} nm away (be at {to:,.0f} ft {margin} nm out): "
                                          f"{when}", foreground=C["amber"] if left <= 0.5 else C["text"])
        elif ap is not None:
            self.dest_line.configure(text=f"{ap.code}: the distance shows once connected to the sim.",
                                     foreground=C["muted"])
        else:
            self.dest_line.configure(text="Type a destination in the main window to see how far out you are.",
                                     foreground=C["muted"])


# ---------------------------------------------------------------- about dialog

ABOUT_TEXT = (
    "Freeware, for Microsoft Flight Simulator 2024 only.\n\n"
    "For entertainment only. Not for real-world aviation, navigation, training or any safety-critical use.\n\n"
    "An independent project. Not affiliated with, endorsed by, sponsored by or supported by Microsoft, "
    "Asobo Studio or PMDG. Their product names are used only to describe compatibility.\n\n"
    "Free software under the GNU AGPL-3.0: you may use, change and share it (LICENSE.txt next to the app). "
    "The source code is on the Old Bird Copilot GitHub page.\n\n"
    "Credits: the voice \"Michael\" is Kokoro (am_michael), with eSpeak NG and ONNX Runtime. Airport data from "
    "OurAirports "
    "(public domain). Details and licences are in the voice folder's THIRD-PARTY-NOTICES.txt."
)


class AboutDialog:
    def __init__(self, main: MainWindow):
        self.win = win = tk.Toplevel(main.root)
        win.title(f"About {APP_NAME}")
        win.configure(bg=C["bg"])
        win.transient(main.root)
        win.resizable(False, False)
        win.iconphoto(False, main._icon)
        win.bind("<Escape>", lambda _e: win.destroy())
        f = ttk.Frame(win, style="Panel.TFrame", padding=20)
        f.grid(sticky="nsew")
        ttk.Label(f, text=APP_NAME, style="CardTitle.TLabel", foreground=C["amber"],
                  font=(FONT, 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(f, text=f"Version {__version__}", style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w",
                                                                                   pady=(0, 12))
        ttk.Label(f, text=ABOUT_TEXT, style="Panel.TLabel", wraplength=440, justify="left").grid(row=2, column=0,
                                                                                                sticky="w")
        data = main.ctl.config_path.parent
        ttk.Label(f, text=f"Your settings and flight recordings:\n{data}", style="PanelMuted.TLabel",
                  wraplength=440, justify="left").grid(row=3, column=0, sticky="w", pady=(14, 6))
        btns = ttk.Frame(f, style="Panel.TFrame")
        btns.grid(row=4, column=0, sticky="ew")
        btns.columnconfigure(1, weight=1)
        ttk.Button(btns, text="Open that folder", command=lambda: _open(data)).grid(row=0, column=0, sticky="w")
        ttk.Button(btns, text="Close", command=win.destroy).grid(row=0, column=2, sticky="e")
        win.update_idletasks()
        x = main.root.winfo_rootx() + (main.root.winfo_width() - win.winfo_width()) // 2
        win.geometry(f"+{max(x, 0)}+{max(main.root.winfo_rooty() + 140, 0)}")
        win.grab_set()


# ------------------------------------------------------------ settings dialog

class SettingsDialog:
    def __init__(self, main: MainWindow):
        self.main = main
        self.ctl = main.ctl
        cfg = self.ctl.cfg
        self.win = win = tk.Toplevel(main.root)
        win.title("Settings")
        win.configure(bg=C["bg"])
        win.transient(main.root)
        win.resizable(False, False)
        win.iconphoto(False, main._icon)

        body = ttk.Frame(win, padding=16)
        body.grid(sticky="nsew")
        nb = ttk.Notebook(body)
        nb.grid(row=0, column=0, sticky="nsew")

        self.v: dict[str, tk.Variable] = {}
        self._build_voice(nb, cfg)
        self._build_callouts(nb, cfg)
        self._build_warnings(nb, cfg)
        self._build_landing(nb, cfg)
        self._build_advanced(nb, cfg)

        btns = ttk.Frame(body)
        btns.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        btns.columnconfigure(0, weight=1)
        ttk.Button(btns, text="Reset to defaults", command=self.reset).grid(row=0, column=0, sticky="w")
        ttk.Button(btns, text="Cancel", command=win.destroy).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self.save).grid(row=0, column=2)

        win.bind("<Escape>", lambda _e: win.destroy())
        win.update_idletasks()
        x = main.root.winfo_rootx() + (main.root.winfo_width() - win.winfo_width()) // 2
        y = main.root.winfo_rooty() + 40
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.grab_set()
        win.focus_set()

    # -- helpers

    def _tab(self, nb, title) -> ttk.Frame:
        f = ttk.Frame(nb, style="Panel.TFrame", padding=16)
        f.columnconfigure(1, weight=1)
        nb.add(f, text=title)
        return f

    def _row_label(self, f, row, text, hint=None):
        ttk.Label(f, text=text, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 14))
        if hint:
            ttk.Label(f, text=hint, style="PanelMuted.TLabel", wraplength=260, justify="left").grid(
                row=row, column=2, sticky="w", padx=(8, 0))

    def _spin(self, f, row, key, text, value, lo, hi, step=1, hint=None, is_float=False):
        var = tk.StringVar(value=config_mod._toml_value(value))
        self.v[key] = var
        self._row_label(f, row, text, hint)
        ttk.Spinbox(f, from_=lo, to=hi, increment=step, textvariable=var, width=8).grid(
            row=row, column=1, sticky="w")
        var._is_float = is_float  # noqa: SLF001 - remembered for parsing
        return var

    def _check(self, f, row, key, text, value, col=0, span=3):
        var = tk.BooleanVar(value=value)
        self.v[key] = var
        ttk.Checkbutton(f, text=text, variable=var, style="Panel.TCheckbutton").grid(
            row=row, column=col, columnspan=span, sticky="w", pady=4)
        return var

    def _section(self, f, row, text):
        ttk.Label(f, text=text, style="Section.TLabel").grid(row=row, column=0, columnspan=3, sticky="w",
                                                            pady=(12 if row else 0, 4))

    # -- tabs

    def _build_voice(self, nb, cfg: Config):
        f = self._tab(nb, "Voice")
        from .speech import MICHAEL_LABEL, WINDOWS_DEFAULT, list_voices, michael_available, wants_michael
        try:
            voices = list_voices()
        except Exception:  # noqa: BLE001 - no SAPI available
            voices = []
        built_in = michael_available()
        choices = ([MICHAEL_LABEL] if built_in else []) + ["Windows default"] + voices
        if built_in and wants_michael(cfg.voice):
            current = MICHAEL_LABEL
        elif cfg.voice and cfg.voice != WINDOWS_DEFAULT:
            current = next((v for v in voices if cfg.voice.lower() in v.lower()), "Windows default")
        else:
            current = "Windows default"
        self.voice_var = tk.StringVar(value=current)
        self._row_label(f, 0, "Voice")
        ttk.Combobox(f, values=choices, textvariable=self.voice_var, state="readonly", width=44).grid(
            row=0, column=1, columnspan=2, sticky="w")

        self.rate_var = tk.IntVar(value=cfg.rate)
        self.volume_var = tk.IntVar(value=cfg.volume)
        for row, (text, var, lo, hi) in enumerate([("Speed", self.rate_var, -10, 10),
                                                   ("Volume", self.volume_var, 0, 100)], start=1):
            self._row_label(f, row, text)
            val = ttk.Label(f, text=str(var.get()), style="Panel.TLabel", width=4)
            val.grid(row=row, column=2, sticky="w", padx=(8, 0))
            ttk.Scale(f, from_=lo, to=hi, orient="horizontal", length=260,
                      command=lambda x, var=var, val=val: (var.set(round(float(x))), val.configure(text=str(var.get()))),
                      value=var.get()).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="A faster voice keeps up better with the 50-40-30-20 countdown.",
                  style="PanelMuted.TLabel").grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 8))
        ttk.Button(f, text="Test these settings", command=self._test_voice).grid(row=4, column=0, columnspan=2,
                                                                                 sticky="w")

        self._section(f, 5, "Custom sounds")
        ttk.Label(f, text="Put <callout>.wav files (50.wav, retard.wav, …) in the sounds folder\n"
                          "to replace the voice for that callout.",
                  style="PanelMuted.TLabel", justify="left").grid(row=6, column=0, columnspan=3, sticky="w")
        ttk.Button(f, text="Open sounds folder", command=self._open_sounds).grid(row=7, column=0, columnspan=2,
                                                                                 sticky="w", pady=(8, 0))

    def _build_callouts(self, nb, cfg: Config):
        f = self._tab(nb, "Callouts")

        self._section(f, 0, "Callout style")
        style = cfg.callout_style
        if style != "custom" and config_mod.style_of(cfg.heights, cfg.retard_ft) != style:
            style = "custom"  # e.g. an older config.toml, or heights edited by hand
        self.style_var = tk.StringVar(value=style)
        radios = ttk.Frame(f, style="Panel.TFrame")
        radios.grid(row=1, column=0, columnspan=3, sticky="w")
        for i, (value, label) in enumerate([("boeing", "Boeing"), ("airbus", "Airbus"), ("custom", "Custom")]):
            ttk.Radiobutton(radios, text=label, value=value, variable=self.style_var, style="Panel.TRadiobutton",
                            command=self._style_picked).grid(row=0, column=i, sticky="w", padx=(0, 22))
        self.style_desc = ttk.Label(f, text="", style="PanelMuted.TLabel", wraplength=520, justify="left")
        self.style_desc.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self._section(f, 3, "Call out these heights (ft above ground)")
        heights = sorted(set(STANDARD_HEIGHTS) | set(cfg.heights) | set(cfg.speed_readout_heights), reverse=True)
        grid = ttk.Frame(f, style="Panel.TFrame")
        grid.grid(row=4, column=0, columnspan=3, sticky="w")
        self.height_vars = {}
        for i, h in enumerate(heights):
            var = tk.BooleanVar(value=h in cfg.heights)
            self.height_vars[h] = var
            ttk.Checkbutton(grid, text=f"{h}", variable=var, style="Panel.TCheckbutton", width=6).grid(
                row=i // 7, column=i % 7, sticky="w", pady=2)

        self._section(f, 5, "Add the speed (\"five hundred, speed one one two\") at")
        grid2 = ttk.Frame(f, style="Panel.TFrame")
        grid2.grid(row=6, column=0, columnspan=3, sticky="w")
        self.speed_vars = {}
        for i, h in enumerate(h for h in heights if h >= 100):
            var = tk.BooleanVar(value=h in cfg.speed_readout_heights)
            self.speed_vars[h] = var
            ttk.Checkbutton(grid2, text=f"{h}", variable=var, style="Panel.TCheckbutton", width=6).grid(
                row=i // 7, column=i % 7, sticky="w", pady=2)

        self._section(f, 7, "Options")
        self._check(f, 8, "require_gear_down", "Only call heights when the gear is down (recommended)",
                    cfg.require_gear_down)
        self._check(f, 12, "flap_callouts", "Say the flap setting when the flaps settle (\"flaps thirty\")",
                    cfg.flap_callouts)
        self._check(f, 13, "wind_check", "Wind check passing 1000 ft (\"wind two seven zero at fifteen, "
                                         "crosswind ten from the left\")", cfg.wind_check)
        self._check(f, 14, "stable_check", "Stable check at 500 ft (\"five hundred, stable\" / \"unstable, speed\")",
                    cfg.stable_check)
        self._check(f, 15, "takeoff_callouts", "Take-off calls (\"vee one\", \"rotate\", \"positive rate\")",
                    cfg.takeoff_callouts)
        extra = ttk.Frame(f, style="Panel.TFrame")
        extra.grid(row=16, column=0, columnspan=3, sticky="w", padx=(24, 0))
        self._check(extra, 0, "call_80kt", "also \"eighty knots\"", cfg.call_80kt, span=1)
        self._check(extra, 0, "call_v2", "also \"vee two\"", cfg.call_v2, col=1, span=1)
        self._check(f, 18, "landing_check", "DC-6 landing check passing 300 ft (\"flaps full, three green\" / "
                                            "\"check flaps\")", cfg.landing_check)
        self._check(f, 17, "crosswind_remarks", "Crew remark on a strong crosswind (25 kt+): \"these are some "
                                                "crosswinds!\"", cfg.crosswind_remarks)
        self._check(f, 9, "approaching_minimums", "Call 100 ft above minimums (\"approaching minimums\" / "
                                                  "\"hundred above\")", cfg.approaching_minimums)
        sub = ttk.Frame(f, style="Panel.TFrame")
        sub.grid(row=10, column=0, columnspan=3, sticky="w", pady=(6, 0))
        sub.columnconfigure(1, weight=1)
        self._spin(sub, 0, "retard_ft", "Say \"retard\" at", cfg.retard_ft, 0, 200,
                   hint="ft   (0 = off, like Boeing: it says \"ten\")")
        self._check(f, 11, "retard_repeat", "Repeat \"retard\" until the throttle is at idle", cfg.retard_repeat)

        # Editing heights or retard by hand switches the style to whatever it now matches (usually Custom).
        for var in self.height_vars.values():
            var.trace_add("write", lambda *_: self._style_from_values())
        self.v["retard_ft"].trace_add("write", lambda *_: self._style_from_values())
        self._update_style_desc()

    _STYLE_DESCRIPTIONS = {
        "boeing": "Twenty five hundred, 1000, 500, 100, 50, 40, 30, 20, 10. No \"retard\": it just says \"ten\". "
                  "Minimums: \"approaching minimums\", \"minimums\".",
        "airbus": "Two thousand five hundred, 1000, 500, 400, 300, 200, 100, 50, 40, 30, 20, then \"retard\" "
                  "at 10 ft. Minimums: \"hundred above\", \"minimum\".",
        "custom": "Your own selection below, with Boeing wording.",
    }

    def _style_picked(self):
        preset = config_mod.STYLE_PRESETS.get(self.style_var.get())
        if preset:
            self._applying_preset = True
            try:
                for h, var in self.height_vars.items():
                    var.set(h in preset["heights"])
                self.v["retard_ft"].set(str(preset["retard_ft"]))
            finally:
                self._applying_preset = False
        self._update_style_desc()

    def _style_from_values(self):
        if getattr(self, "_applying_preset", False):
            return
        try:
            retard = int(float(self.v["retard_ft"].get() or 0))
        except ValueError:
            return  # still typing
        heights = [h for h, var in self.height_vars.items() if var.get()]
        self.style_var.set(config_mod.style_of(heights, retard))
        self._update_style_desc()

    def _update_style_desc(self):
        self.style_desc.configure(text=self._STYLE_DESCRIPTIONS[self.style_var.get()])

    def _build_warnings(self, nb, cfg: Config):
        f = self._tab(nb, "Warnings")
        self._section(f, 0, "Speed on final (gear down, 1000 to 50 ft; needs Vref)")
        self._spin(f, 1, "vref_kt", "Default Vref", cfg.vref_kt, 0, 400, hint="kt   (0 = off; you can change it "
                                                                             "per flight in the main window)")
        self.speed_mode_var = tk.StringVar(value=cfg.speed_callouts)
        modes = ttk.Frame(f, style="Panel.TFrame")
        modes.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 2))
        ttk.Radiobutton(modes, text="Deviation: \"plus ten\", \"minus five\" (when it changes)", value="deviation",
                        variable=self.speed_mode_var, style="Panel.TRadiobutton").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(modes, text="Warnings: \"speed high\", \"speed low\" (repeats)", value="warnings",
                        variable=self.speed_mode_var, style="Panel.TRadiobutton").grid(row=1, column=0, sticky="w")
        self.plus_var = tk.StringVar(value=", ".join(str(k) for k in sorted(cfg.speed_plus_kt)))
        self.minus_var = tk.StringVar(value=", ".join(str(k) for k in sorted(cfg.speed_minus_kt)))
        self._row_label(f, 3, "Deviation: plus at", "kt above Vref")
        ttk.Entry(f, textvariable=self.plus_var, width=12).grid(row=3, column=1, sticky="w")
        self._row_label(f, 4, "Deviation: minus at", "kt below Vref")
        ttk.Entry(f, textvariable=self.minus_var, width=12).grid(row=4, column=1, sticky="w")
        self._spin(f, 5, "speed_low_margin_kt", "Warnings: \"speed low\" below Vref −", cfg.speed_low_margin_kt,
                   0, 100, hint="kt")
        self._spin(f, 6, "speed_high_margin_kt", "Warnings: \"speed high\" above Vref +", cfg.speed_high_margin_kt,
                   0, 200, hint="kt   (0 = off)")
        self._section(f, 7, "Other warnings (between 1000 and 50 ft)")
        self._spin(f, 8, "sink_rate_fpm", "\"Sink rate\" faster than", cfg.sink_rate_fpm, 0, 10_000, step=100,
                   hint="fpm   (0 = off)")
        self._spin(f, 9, "gear_warning_ft", "\"Too low, gear\" below", cfg.gear_warning_ft, 0, 5000, step=50,
                   hint="ft with gear up   (0 = off)")
        self._spin(f, 10, "repeat_interval_s", "Repeat warnings every", cfg.repeat_interval_s, 0.5, 600, step=1,
                   hint="seconds", is_float=True)
        self._check(f, 11, "overspeed_warnings", "\"Flap speed\" / \"gear speed\" (uses the aircraft's own limits)",
                    cfg.overspeed_warnings)

    def _build_landing(self, nb, cfg: Config):
        f = self._tab(nb, "Landing")
        self._section(f, 0, "Touchdown target (e.g. NeoFly soft-landing missions)")
        self._spin(f, 1, "landing_target_fpm", "Default max landing", cfg.landing_target_fpm, 0, 2000, step=10,
                   hint="fpm   (0 = off; you can change it per flight in the main window)")
        self._check(f, 2, "speak_landing_result", "Say the result after touchdown (\"one eight two, within target\")",
                    cfg.speak_landing_result)
        self._check(f, 3, "announce_touchdown", "Say the touchdown rate even when no target is set",
                    cfg.announce_touchdown)
        self._section(f, 4, "Rollout callouts")
        self.rollout_var = tk.StringVar(value=", ".join(str(k) for k in cfg.rollout_callouts_kt))
        self._row_label(f, 5, "Call these speeds (kt)", "e.g. 80, 60   (empty = off)")
        ttk.Entry(f, textvariable=self.rollout_var, width=16).grid(row=5, column=1, sticky="w")
        self._section(f, 6, "Descent planning (type a destination in the main window)")
        self._check(f, 7, "descent_calls", "Say \"top of descent\", \"high on profile\" (and in DC-6 mode \"five "
                                           "hundred to level off\")", cfg.descent_calls)
        self._spin(f, 8, "descent_rate_fpm", "Descent rate", cfg.descent_rate_fpm, 100, 5000, step=100,
                   hint="fpm   (slide it per flight in the calculator)")
        self._spin(f, 9, "descent_pattern_ft", "Arrive at", cfg.descent_pattern_ft, 0, 10_000, step=100,
                   hint="ft above the field ...")
        self._spin(f, 10, "descent_margin_nm", "... this far out", cfg.descent_margin_nm, 0, 50,
                   hint="nm   (room to slow down and configure)")
        ttk.Label(f, text="Universal mode. DC-6 mode flies the DC-6 profile: 1,000 fpm to a level-off 3,000 ft above "
                          "the field about 30 nm out.", style="PanelMuted.TLabel", wraplength=520,
                  justify="left").grid(row=11, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self._check(f, 12, "request_descent_call", "Say \"request descent\" 2 minutes before the top of descent "
                                                   "(time to ask ATC)", cfg.request_descent_call)

    def _build_advanced(self, nb, cfg: Config):
        f = self._tab(nb, "Advanced")
        self._spin(f, 0, "agl_offset_ft", "Height offset", cfg.agl_offset_ft, -100, 100, hint="ft   (if the height "
                   "doesn't read ~0 on the runway)", is_float=True)
        self.throttle_var = tk.StringVar(value=cfg.throttle_simvar)
        self._row_label(f, 1, "Throttle SimVar")
        ttk.Entry(f, textvariable=self.throttle_var, width=44).grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Label(f, text="Used for the retard repeat. Empty = off. Check the THROTTLE readout follows your levers.",
                  style="PanelMuted.TLabel", wraplength=520, justify="left").grid(
            row=2, column=0, columnspan=3, sticky="w")
        self._section(f, 3, "Flight recorder")
        self._check(f, 4, "record_flights", "Record each session to the flights folder (newest 20 kept)",
                    cfg.record_flights)
        ttk.Label(f, text="Helps track down bugs. Press ⚑ Flag moment when something sounds wrong.",
                  style="PanelMuted.TLabel", wraplength=520, justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w")
        ttk.Button(f, text="Open flights folder", command=self._open_flights).grid(
            row=6, column=0, sticky="w", pady=(8, 0))
        self._section(f, 7, "Settings file")
        ttk.Label(f, text=str(self.ctl.config_path), style="PanelMuted.TLabel", wraplength=520,
                  justify="left").grid(row=8, column=0, columnspan=3, sticky="w")
        ttk.Button(f, text="Open folder", command=lambda: _open(self.ctl.config_path.parent)).grid(
            row=9, column=0, sticky="w", pady=(8, 0))
        ttk.Label(f, text=f"{APP_TITLE}, version {__version__}", style="PanelMuted.TLabel").grid(
            row=10, column=0, columnspan=3, sticky="w", pady=(16, 0))

    # -- actions

    def _collect(self) -> Config:
        """Build a validated Config from the form. Raises ConfigError."""
        data = {}
        for key, var in self.v.items():
            if isinstance(var, tk.BooleanVar):
                data[key] = var.get()
                continue
            text = var.get().strip()
            try:
                data[key] = float(text) if getattr(var, "_is_float", False) else int(float(text))
            except ValueError:
                raise config_mod.ConfigError(f"'{text}' is not a number ({key.replace('_', ' ')})") from None
        data["callout_style"] = self.style_var.get()
        data["heights"] = [h for h, var in self.height_vars.items() if var.get()]
        data["speed_readout_heights"] = [h for h, var in self.speed_vars.items() if var.get()]
        try:
            data["rollout_callouts_kt"] = [int(x) for x in self.rollout_var.get().replace(",", " ").split()]
        except ValueError:
            raise config_mod.ConfigError("Rollout speeds must be whole numbers, like 80, 60") from None
        data["speed_callouts"] = self.speed_mode_var.get()
        for key, var, example in (("speed_plus_kt", self.plus_var, "10, 15, 20"),
                                  ("speed_minus_kt", self.minus_var, "5, 10")):
            try:
                data[key] = [int(x) for x in var.get().replace(",", " ").split()]
            except ValueError:
                raise config_mod.ConfigError(f"Deviation speeds must be whole numbers, like {example}") from None
        from .speech import MICHAEL_LABEL, WINDOWS_DEFAULT
        voice = self.voice_var.get()
        data["voice"] = {MICHAEL_LABEL: "", "Windows default": WINDOWS_DEFAULT}.get(voice, voice)
        data["rate"] = self.rate_var.get()
        data["volume"] = self.volume_var.get()
        data["throttle_simvar"] = self.throttle_var.get().strip()
        old = self.ctl.cfg
        data["sounds_dir"] = old.sounds_dir
        data["height_callouts"] = old.height_callouts   # switched in the main window
        data["v1_kt"], data["vr_kt"], data["v2_kt"] = old.v1_kt, old.vr_kt, old.v2_kt
        data["texts"] = dict(old.texts)
        return config_mod.from_dict(data, base_dir=old.base_dir)

    def save(self):
        try:
            new = self._collect()
        except config_mod.ConfigError as e:
            messagebox.showerror("Check your settings", str(e), parent=self.win)
            return
        old = self.ctl.cfg
        try:
            self.ctl.apply_settings(new)
        except OSError as e:
            messagebox.showerror("Couldn't save settings", f"Couldn't write {self.ctl.config_path}:\n{e}",
                                 parent=self.win)
            return
        self.main.log(f"Settings saved to {self.ctl.config_path.name}.")
        # Flight values still at the old default follow the new default.
        if self.ctl.flight_vref == old.vref_kt and new.vref_kt != old.vref_kt:
            self.main.set_flight_fields(new.vref_kt, self.ctl.flight_minimums)
        if (self.ctl.flight_landing_target == old.landing_target_fpm
                and new.landing_target_fpm != old.landing_target_fpm):
            self.main.target_var.set(str(new.landing_target_fpm))
            self.main._apply_flight()
        self.main._update_flight_hint()
        self.win.destroy()

    def reset(self):
        if not messagebox.askyesno("Reset settings", "Put every setting back to its default?", parent=self.win):
            return
        default = dataclasses.replace(Config(), base_dir=self.ctl.cfg.base_dir)
        self.win.destroy()
        self.ctl.apply_settings(default)
        self.main.log("Settings reset to defaults.")
        self.main._update_flight_hint()
        SettingsDialog(self.main)

    def _test_voice(self):
        try:
            cfg = self._collect()
        except config_mod.ConfigError as e:
            messagebox.showerror("Check your settings", str(e), parent=self.win)
            return

        def run():
            from .speech import make_speaker
            sp = make_speaker(cfg, log=self.ctl.log)
            sp.say(Callout("500", f"{cfg.text('500', 'five hundred')}, speed one one two", 10))
            for c in countdown_sample(cfg):
                sp.say(c)
            sp.wait_idle(20)
            sp.stop()

        threading.Thread(target=run, daemon=True).start()

    def _open_flights(self):
        path = self.ctl.recorder.folder
        path.mkdir(parents=True, exist_ok=True)
        _open(path)

    def _open_sounds(self):
        path = self.ctl.cfg.sounds_path
        path.mkdir(parents=True, exist_ok=True)
        _open(path)


def _open(path) -> None:
    try:
        os.startfile(path)  # type: ignore[attr-defined]  # Windows only
    except (AttributeError, OSError) as e:
        messagebox.showerror("Couldn't open folder", str(e))


# --------------------------------------------------------------------- entry

def _already_running() -> bool:
    """True if another Old Bird Copilot window is open (two copies would speak every callout twice)."""
    global _instance_mutex
    try:
        import ctypes
        _instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "LandingCallouts.SingleInstance")
        return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001 - not Windows
        return False


_instance_mutex = None  # held for the life of the process

FIT_MIN_PCT = 75           # never shrink the text below this (% of normal)
TITLE_BAR_PX = 45          # window title bar and frame at 100 %


def screen_work_height(root: tk.Tk) -> int:
    """Usable screen height in pixels (without the taskbar)."""
    try:
        import ctypes
        import ctypes.wintypes
        rect = ctypes.wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):   # SPI_GETWORKAREA
            return rect.bottom - rect.top
    except Exception:  # noqa: BLE001 - not Windows
        pass
    return root.winfo_screenheight()


def windows_scaling_pct(root: tk.Tk) -> float:
    """Windows' display scaling in % (the app is DPI aware, so this is the real setting)."""
    return root.winfo_fpixels("1i") / 96 * 100


def build_fitted_window(ctl: Controller) -> tuple[tk.Tk, "MainWindow", int]:
    """Build the main window, shrinking the text until it fits the screen (e.g. a 1080p laptop at 150 %,
    where everything at Windows' own size would be ~1,100 px tall). Returns (root, window, text %)."""
    pct = None
    attempts = 5
    for attempt in range(attempts):
        root = tk.Tk()
        windows_pct = windows_scaling_pct(root)
        pct = windows_pct if pct is None else pct
        if pct < windows_pct:
            root.tk.call("tk", "scaling", 96 / 72 * pct / 100)
        apply_theme(root)
        window = MainWindow(root, ctl)
        usable = screen_work_height(root) - TITLE_BAR_PX * windows_pct / 100
        need = window.needed_height()
        if need <= usable or pct <= FIT_MIN_PCT or attempt == attempts - 1:
            return root, window, round(pct)   # the last try is kept even if it's still a little tall
        # text is only part of the height (spacing stays): aim a little lower than the plain ratio
        pct = max(FIT_MIN_PCT, pct * (usable / need) ** 1.3)
        root.destroy()
    raise AssertionError("unreachable")


def run(cfg: Config, config_path, use_sim: bool = True, warnings: list[str] | None = None) -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except Exception:  # noqa: BLE001 - not Windows, or an old Windows
        pass

    if use_sim and _already_running():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_TITLE, f"{APP_NAME} is already open. Look for it on the taskbar.")
        root.destroy()
        return

    ctl = Controller(cfg, config_path, use_sim=use_sim)
    root, window, scaling = build_fitted_window(ctl)
    # Errors inside button handlers etc. go to the Activity log instead of a console nobody sees.
    root.report_callback_exception = lambda exc, val, tb: ctl.log(f"Error: {exc.__name__}: {val}")
    config_mod.warning_handler = lambda msg: ctl.log(f"Settings warning: {msg}")
    ctl.start()
    window.log(f"{APP_TITLE} {__version__}. Settings: {config_path}")
    windows_pct = round(windows_scaling_pct(root))
    if scaling < windows_pct:
        window.log(f"Text scaled to {scaling}% (Windows is at {windows_pct}%) so the window fits the screen.")
    for w in warnings or []:
        window.log(f"Settings warning: {w}")
    root.mainloop()
