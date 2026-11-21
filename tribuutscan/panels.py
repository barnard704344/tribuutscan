"""The viewer's right-hand panels: one small class per group of settings or actions.

A Panel has a header that folds its body; `build(body)` makes its widgets once and `refresh()` updates
them (the viewer calls it about 30 times a second). Panels only call the viewer App's methods and read
its state, so adding a panel is one class here plus one entry in viewer.PANELS.
"""
import math
import tkinter as tk
from tkinter import ttk

from . import cloud

BG, SURFACE = "#f0f3f7", "#ffffff"
INK, MUTED = "#172338", "#47576d"
LINE, HOVER, PRESSED = "#d2dae5", "#edf2f8", "#dce7f4"
ACCENT, ACCENT_HOVER, ACCENT_PRESSED = "#174a85", "#123d70", "#10345e"
BOLD = ("TkDefaultFont", 10, "bold")


def configure_styles(root):
    """Flat, app-local colours and visible keyboard focus on the Tk capture window."""
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Capture.TButton", background=SURFACE, foreground=INK, bordercolor=LINE,
                    lightcolor=SURFACE, darkcolor=SURFACE, relief="flat", padding=(9, 5))
    style.map("Capture.TButton",
              background=[("disabled", BG), ("pressed", PRESSED), ("active", HOVER)],
              foreground=[("disabled", MUTED)],
              bordercolor=[("focus", ACCENT)], focuscolor=[("focus", ACCENT)])
    style.configure("Primary.Capture.TButton", background=ACCENT, foreground=SURFACE,
                    font=BOLD, padding=(10, 6))
    style.map("Primary.Capture.TButton",
              background=[("disabled", BG), ("pressed", ACCENT_PRESSED), ("active", ACCENT_HOVER)],
              foreground=[("disabled", MUTED), ("!disabled", SURFACE)],
              focuscolor=[("focus", SURFACE)])
    style.configure("Header.Capture.TButton", background=BG, font=BOLD, anchor="w", padding=(8, 5))
    style.configure("Capture.TCheckbutton", background=SURFACE, foreground=INK,
                    indicatorbackground=SURFACE, padding=(3, 2))
    style.map("Capture.TCheckbutton",
              background=[("active", HOVER)],
              indicatorbackground=[("selected", ACCENT), ("!selected", SURFACE)],
              indicatorforeground=[("selected", SURFACE)],
              focuscolor=[("focus", ACCENT)])
    style.configure("Capture.TMenubutton", background=SURFACE, foreground=INK, padding=(6, 4),
                    bordercolor=LINE, lightcolor=SURFACE, darkcolor=SURFACE, relief="flat")
    style.map("Capture.TMenubutton", background=[("pressed", PRESSED), ("active", HOVER)],
              bordercolor=[("focus", ACCENT)], focuscolor=[("focus", ACCENT)])


def button(parent, **kwargs):
    kwargs.setdefault("style", "Capture.TButton")
    return ttk.Button(parent, **kwargs)


def checkbox(parent, **kwargs):
    return ttk.Checkbutton(parent, style="Capture.TCheckbutton", **kwargs)


def frame(parent):
    return tk.Frame(parent, bg=SURFACE)


def menu(parent, variable, choices, command=None):
    # ttk.OptionMenu takes an explicit default before the list of options.
    option = ttk.OptionMenu(parent, variable, variable.get(), *choices,
                            style="Capture.TMenubutton", command=command)
    option["menu"].configure(background=SURFACE, foreground=INK, activebackground=HOVER,
                             activeforeground=INK, relief="flat", borderwidth=1)
    return option


class Panel(tk.Frame):
    title = ""

    def __init__(self, parent, app, open_=True):
        super().__init__(parent, bg=SURFACE, bd=0, highlightthickness=1, highlightbackground=LINE)
        self.app, self.open, self.shown = app, open_, {}
        self.header = button(self, style="Header.Capture.TButton", command=self.toggle)
        self.header.pack(fill="x")
        self.body = frame(self)
        self.build(self.body)
        self._place()

    def _place(self):
        self.header.configure(text=("▾ " if self.open else "▸ ") + self.title)
        if self.open:
            self.body.pack(fill="x", padx=8, pady=(3, 5))
        else:
            self.body.pack_forget()

    def toggle(self):
        self.open = not self.open
        self._place()

    def set(self, widget, **options):
        """configure() only when something changed (refresh runs every tick)."""
        if self.shown.get(widget) != options:
            self.shown[widget] = options
            widget.configure(**options)

    def build(self, body):
        pass

    def refresh(self):
        pass


class ScannerPanel(Panel):
    """Depth range, IR exposure and gain (Auto IR: the scanner's own auto exposure), colour brightness."""
    title = "Scanner"

    def build(self, body):
        row = frame(body)
        row.pack(fill="x", pady=(2, 4))
        tk.Label(row, text="Range", bg=SURFACE, fg=MUTED, padx=4).pack(side="left")
        menu(row, self.app.range_var, self.app.ranges, self.app.choose_range).pack(side="left", fill="x")
        for label, node, cid in self.app.sliders:
            self.app.slider(body, label, node, cid)
        buttons = frame(body)
        buttons.pack(fill="x", pady=(4, 0))
        checkbox(buttons, text="Auto IR", variable=self.app.ir_auto_var,
                 command=self.app.toggle_ir_auto).pack(side="left", expand=True)
        button(buttons, text="Reset defaults", command=self.app.reset_controls).pack(side="left", expand=True, fill="x")


class CapturePanel(Panel):
    """Save a frame set, record raw sets (with IMU samples), mesh the latest recording."""
    title = "Capture"

    def build(self, body):
        button(body, text="Save frame (s)", command=self.app.save).pack(fill="x")
        row = frame(body)
        row.pack(fill="x", pady=(2, 0))
        self.record = button(row, command=self.app.toggle_record)
        self.record.pack(side="left", expand=True, fill="x", padx=(0, 5))
        menu(row, self.app.record_rate, self.app.record_rates).pack(side="left")
        self.mesh = button(body, command=self.app.mesh)
        self.mesh.pack(fill="x", pady=(2, 0))

    def refresh(self):
        self.set(self.record, text="Stop recording (r)" if self.app.rec["recorder"] else "Record (r)")
        busy = self.app.job["process"] is not None
        self.set(self.mesh, text="Meshing..." if busy else "Mesh last recording (m)", state="disabled" if busy else "normal")


class ScanPanel(Panel):
    """Live scan into one merged cloud (worker process), pause, and continue."""
    title = "Scan"
    STATES = {"idle": ("Start scan (c)", "normal"), "counting": ("Cancel start (c / Esc)", "normal"),
              "scanning": ("Stop & Save (c)", "normal"), "pausing": ("Stop & Save (c)", "normal"),
              "paused": ("Stop & Save (c)", "normal"), "resuming": ("Stop & Save (c)", "normal"),
              "cancelling": ("Cancelling...", "disabled"),
              "saving": ("Saving...", "disabled"), "optimising": ("Optimising...", "disabled"),
              "meshing": ("Making the mesh...", "disabled")}

    def build(self, body):
        row = frame(body)
        row.pack(fill="x", pady=(2, 0))
        menu(row, self.app.preset, cloud.PRESETS_MM).pack(side="left")
        self.scan = button(row, style="Primary.Capture.TButton", command=self.app.toggle_scan)
        self.scan.pack(side="left", expand=True, fill="x", padx=(5, 0))
        row = frame(body)
        row.pack(fill="x", pady=(2, 0))
        self.pause = button(row, command=self.app.toggle_pause_scan)
        self.pause.pack(side="left", expand=True, fill="x", padx=(0, 5))
        self.cancel = button(row, text="Cancel scan", command=self.app.cancel_scan)
        self.cancel.pack(side="left", expand=True, fill="x")
        self.separate = button(body, text="Start separate piece", state="disabled",
                               command=self.app.start_separate_piece)
        self.separate.pack(fill="x", pady=(2, 0))
        row = frame(body)
        row.pack(fill="x", pady=(2, 0))
        button(row, text="Continue last", command=self.app.continue_scan).pack(
            side="left", expand=True, fill="x")
        row = frame(body)
        row.pack(fill="x", pady=(2, 0))
        checkbox(row, text="Remove base", variable=self.app.remove_base).pack(side="left")
        checkbox(row, text="keep raw frames", variable=self.app.keep_raw).pack(side="left")
        row = frame(body)
        row.pack(fill="x", pady=(2, 0))
        checkbox(row, text="Mesh after Stop", variable=self.app.mesh_after).pack(side="left")
        checkbox(row, text="Countdown", variable=self.app.countdown).pack(side="left")

    def refresh(self):
        text, state = self.STATES[self.app.scan_state]
        self.set(self.scan, text=text, state=state)
        phase = self.app.scan_state
        label = {"paused": "Resume scan (p)", "pausing": "Finishing frames...", "resuming": "Resuming..."}.get(
            phase, "Pause & Inspect (p)")
        self.set(self.pause, text=label, state="normal" if phase in ("scanning", "paused") else "disabled")
        can_cancel = phase in ("counting", "scanning", "pausing", "paused", "resuming")
        self.set(self.cancel, state="normal" if can_cancel else "disabled")
        self.set(self.separate, state="normal" if phase == "scanning"
                 and self.app.can_start_separate_piece() else "disabled")


class ImuPanel(Panel):
    """The IMU live: a horizon from gravity (tilt), the turn rate, still or moving."""
    title = "IMU"
    W, H = 240, 120

    def build(self, body):
        checkbox(body, text="IMU on", variable=self.app.imu_wanted, command=self.app.toggle_imu).pack(anchor="w")
        self.canvas = tk.Canvas(body, width=self.W, height=self.H, bg="#9ecbea", highlightthickness=0)
        self.canvas.pack(pady=2)
        self.ground = self.canvas.create_polygon(0, 0, 0, 0, fill="#8d6e4a", outline="")
        self.canvas.create_line(self.W / 2 - 30, self.H / 2, self.W / 2 - 8, self.H / 2, width=3)
        self.canvas.create_line(self.W / 2 + 8, self.H / 2, self.W / 2 + 30, self.H / 2, width=3)
        self.bar = self.canvas.create_rectangle(0, self.H - 8, 0, self.H, fill="#c62828", outline="")
        self.tilt = tk.Label(body, anchor="w", bg=SURFACE, fg=INK, font=("TkFixedFont", 10))
        self.tilt.pack(fill="x")
        self.motion = tk.Label(body, anchor="w", bg=SURFACE, fg=INK, font=("TkFixedFont", 10))
        self.motion.pack(fill="x")
        self.ticks = 0

    def refresh(self):
        self.ticks += 1
        if self.ticks % 2:  # 15 Hz is plenty for a horizon
            return
        st = self.app.imu.state() if self.app.imu is not None else None
        if st is None:
            self.set(self.tilt, text=self.app.imu_note or ("IMU lost: switch it off and on" if self.app.imu else "IMU off"))
            self.set(self.motion, text="", fg=INK)
            self.canvas.coords(self.ground, 0, 0, 0, 0)
            self.canvas.coords(self.bar, 0, self.H - 8, 0, self.H)
            return
        w, h = self.W, self.H
        offset = st["pitch"] / 45 * h / 2  # +-45 degrees of pitch fill the view
        a = math.radians(st["roll"])
        dx, dy = math.cos(a) * w * 2, math.sin(a) * w * 2
        cx, cy = w / 2, h / 2 + offset
        nx, ny = -math.sin(a) * h * 4, math.cos(a) * h * 4  # towards the ground side
        self.canvas.coords(self.ground, cx - dx, cy - dy, cx + dx, cy + dy, cx + dx + nx, cy + dy + ny, cx - dx + nx, cy - dy + ny)
        self.canvas.coords(self.bar, 0, h - 8, w * min(st["turn"], 90) / 90, h)
        self.set(self.tilt, text=f"pitch {st['pitch']:+6.1f} deg   roll {st['roll']:+6.1f} deg")
        state = "still" if st["still"] else "moving"
        self.set(self.motion, text=f"turn {st['turn']:5.1f} deg/s {state:6}  {st['rate']:3.0f} Hz  {st['temp']:4.1f} C",
                 fg="#286333" if st["still"] else "#92510a")


class ViewPanel(Panel):
    """What the previews show."""
    title = "View"

    def build(self, body):
        checkbox(body, text="Find markers (ringed in colour)", variable=self.app.find_markers,
                 command=self.app.toggle_markers).pack(anchor="w")
        checkbox(body, text="Hold depth while not scanning (dimmer held pixels)", variable=self.app.hold_depth,
                 command=self.app.toggle_hold).pack(anchor="w")
