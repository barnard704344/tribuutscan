"""Optional legacy Tkinter interface; the default launcher now starts webapp."""
import tkinter as tk
import tkinter.font
import tkinter.messagebox
from . import controller, panels
from .controller import *

PANELS = [panels.ScanPanel, panels.ScannerPanel, panels.CapturePanel, panels.ImuPanel, panels.ViewPanel]

class DistanceGauge(tk.Frame):
    """Text and a three-zone strip give the reading without requiring colour perception."""
    def __init__(self, parent):
        super().__init__(parent, bg=panels.SURFACE, bd=0)
        self.reading = tk.Label(self, anchor="w", bg=panels.SURFACE, fg=panels.INK, font=panels.BOLD)
        self.reading.pack(fill="x")
        strip = tk.Frame(self, bg=panels.LINE, padx=1, pady=1)
        strip.pack(fill="x", pady=(3, 0))
        self.zones = []
        for i, text in enumerate(DISTANCE_STATES):
            label = tk.Label(strip, text=text, pady=3, font=panels.BOLD)
            label.grid(row=0, column=i, sticky="ew", padx=(0, 1 if i < 2 else 0))
            strip.columnconfigure(i, weight=1, uniform="distance")
            self.zones.append(label)
        self.shown = None
        self.show(None, "Waiting for live depth")

    def show(self, active, text):
        if self.shown == (active, text):
            return
        if self.shown is None or self.shown[0] != active:
            for i, label in enumerate(self.zones):
                selected = i == active
                label.configure(text=("● " if selected else "") + DISTANCE_STATES[i],
                                bg=(EDGE, GOOD, EDGE)[i] if selected else panels.BG,
                                fg=panels.SURFACE if selected else panels.MUTED)
        self.reading.configure(text=text)
        self.shown = (active, text)


class App(controller.Controller):
    worker_port = 8765

    def __init__(self, root):
        self.root = root
        super().__init__()

    def variable(self, value):
        return tk.BooleanVar(master=self.root, value=value) if isinstance(value, bool) else tk.StringVar(master=self.root, value=value)

    def start(self):
        super().start()
        self.tick()

    def close(self):
        self.model_pane.close()
        super().close()
        self.root.destroy()


    def build_window(self):
        root = self.root
        root.title("triBUUTSCAN (Ferret Pro)")
        root.configure(bg=panels.BG)
        panels.configure_styles(root)
        main = tk.Frame(root, bg=panels.BG)  # previews, with Status and Messages below
        right = tk.Frame(root, bg=panels.BG)
        main.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=(8, 4), pady=8)
        right.grid(row=0, column=2, sticky="n", padx=(4, 8), pady=8)
        root.rowconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        main.columnconfigure(1, weight=1)
        left, middle = panels.frame(main), panels.frame(main)
        left.grid(row=0, column=0, sticky="new", padx=(0, 4))
        middle.grid(row=0, column=1, sticky="n", padx=(4, 0))
        from .model_pane import ModelPane
        self.model_pane = ModelPane(left, width=MODEL_W, height=MODEL_H)
        self.model_pane.pack(fill="x")
        boxes = tk.Frame(left, bg=panels.BG)
        boxes.pack(fill="both", expand=True, pady=(6, 0))
        self.info = {}
        mono = tkinter.font.nametofont("TkFixedFont").copy()  # (("TkFixedFont", 9) is not monospace: a family name)
        mono.configure(size=9)
        for col, (name, indent) in enumerate((("Status", " " * LABEL_W), ("Messages", "[00:00:00] "))):
            box = panels.frame(boxes)
            box.grid(row=0, column=col, sticky="nsew", padx=(0, 8) if col == 0 else 0)
            boxes.columnconfigure(col, weight=1, uniform="info")
            tk.Label(box, text=name, bg=panels.SURFACE, fg=panels.INK, font=panels.BOLD).pack(
                anchor="w", padx=8, pady=(5, 0))
            text = tk.Text(box, width=30, height=7, wrap="word", font=mono, relief="flat", padx=8, pady=4,
                           bg=panels.SURFACE, fg=panels.INK, highlightthickness=0, bd=0,
                           state="disabled", takefocus=0)
            text.tag_configure("hang", lmargin2=mono.measure(indent))  # wrapped lines start under the text
            text.pack(fill="both", expand=True)
            self.info[name] = text
        boxes.rowconfigure(0, weight=1)
        self.return_panel = panels.frame(left)
        tk.Label(self.return_panel, text="Last tracked view", bg=panels.SURFACE, fg=panels.INK,
                 font=panels.BOLD).pack(anchor="w", padx=8, pady=(4, 0))
        self.return_image = tk.Label(self.return_panel, bg="#101928", bd=0, highlightthickness=0)
        self.return_image.pack(padx=8, pady=4)
        self.return_note = tk.Label(self.return_panel, anchor="w", justify="left", wraplength=MODEL_W - 16,
                                    bg=panels.SURFACE, fg=panels.INK)
        self.return_note.pack(fill="x", padx=8, pady=(0, 5))
        self.views = {}
        depth = panels.frame(middle)
        depth.pack(fill="x")
        cameras = panels.frame(middle)
        cameras.pack(fill="x", pady=(6, 0))
        colour, ir = panels.frame(cameras), panels.frame(cameras)
        colour.pack(side="left")
        ir.pack(side="right")
        for name, parent, width in (("Colour", colour, SIDE_W), ("IR (flipped)", ir, SIDE_W),
                                    ("Depth", depth, DEPTH_W)):
            title_parent = parent
            if name == "Depth":
                title_parent = panels.frame(parent)
                title_parent.pack(fill="x", padx=8, pady=3)
                panels.menu(title_parent, self.preview_mode, (LIVE_DEPTH, COVERAGE),
                            self.choose_depth_preview).pack(side="right")
            title = tk.Label(title_parent, text=depth_title(self.depth.cap) if name == "Depth" else name,
                             bg=panels.SURFACE, fg=panels.INK, font=panels.BOLD)
            if name == "Depth":
                title.pack(side="left")
            else:
                title.pack(anchor="w", padx=8, pady=4)
            blank = tk.PhotoImage(width=width, height=width * fr.H // fr.W)
            self.images[name] = blank
            image = tk.Label(parent, image=blank, bg="#101928", bd=0, highlightthickness=0)
            image.pack()
            if name in ("Depth", "Colour"):  # the aim: a click picks the object there, a right-click lets go
                image.bind("<Button-1>", lambda e, name=name: self.aim_click(name, e.x, e.y))
                image.bind("<Button-3>", lambda _e: self.aim_clear())
            if name == "Depth":
                self.distance = DistanceGauge(parent)
                self.distance.pack(fill="x", padx=8, pady=(6, 0))
                bar = panels.frame(parent)
                bar.pack(fill="x", padx=8, pady=(5, 0))
                self.guide = tk.Label(bar, anchor="center", bg=panels.SURFACE, fg=panels.INK,
                                      font=("TkDefaultFont", 11, "bold"), wraplength=460)
                self.guide.pack(side="left", fill="x", expand=True)
                self.retry = panels.button(bar, text="Retry now", command=self.retry_now)  # shown while lost
                self.coverage_panel = panels.frame(parent)
                legend = panels.frame(self.coverage_panel)
                legend.pack(fill="x")
                for (label, colour), pattern in zip(COVERAGE_SWATCHES, ("solid", "stripes", "dots")):
                    tk.Label(legend, text=f"{label} ({pattern})", bg=colour, fg=panels.INK,
                             font=panels.BOLD, padx=6, pady=2).pack(side="left", padx=(0, 3))
                self.coverage_summary = tk.Label(self.coverage_panel, anchor="w", justify="left",
                                                 wraplength=DEPTH_W - 16, bg=panels.SURFACE, fg=panels.INK)
                self.coverage_summary.pack(fill="x", pady=(2, 0))
                self.coverage_shown = None
            stats = tk.Label(parent, anchor="w", bg=panels.SURFACE, fg=panels.MUTED, font=("TkFixedFont", 9))
            stats.pack(fill="x", padx=8, pady=(2, 5))
            self.views[name] = (title, image, stats)
        self.note = tk.Label(root, anchor="w", bg=panels.SURFACE, fg=panels.INK, padx=8, pady=5,
                             font=("TkDefaultFont", 10))
        self.note.grid(row=1, column=0, columnspan=3, sticky="we", padx=8, pady=(0, 8))
        for cls in PANELS:
            panel = cls(right, self)
            panel.pack(fill="x", pady=(0, 6))
            self.panels.append(panel)
        for key, action in (("s", self.save), ("r", self.toggle_record), ("m", self.mesh), ("c", self.toggle_scan),
                            ("p", self.toggle_pause_scan)):
            root.bind(key, lambda _event, action=action: action())
        root.bind("<Escape>", lambda _event: self.escape())
        root.protocol("WM_DELETE_WINDOW", self.close)

    def tick(self):
        self.poll_capture()
        for name, s in self.streams.items():
            _, image, stats = self.views[name]
            if s.latest is not None and self.shown.get(name) != s.version:  # redraw only a changed preview
                self.shown[name] = s.version
                self.images[name] = tk.PhotoImage(data=s.latest, format="PPM")
                image.configure(image=self.images[name])
            stats.configure(text=f"{s.fps:4.1f} fps   {s.stats.get('text', '')}")
        now = time.monotonic()
        if self.preview_mode.get() == COVERAGE:
            caption = coverage_status(self.depth.stats, now, self.scan_state, self.lost or self.switching["busy"],
                                      scan_view["lost"], self.return_photo is not None)
            if self.coverage_shown != caption:
                self.coverage_summary.configure(text=caption)
                if self.coverage_shown is None:
                    self.coverage_panel.pack(fill="x", padx=8, pady=(4, 0), before=self.distance)
                self.coverage_shown = caption
        elif self.coverage_shown is not None:
            self.coverage_panel.pack_forget()
            self.coverage_shown = None
        if self.lost or self.switching["busy"]:
            self.distance.show(None, "Distance unavailable · " + ("Scanner reconnecting" if self.lost else
                                                                "Changing depth range"))
        else:
            self.distance.show(*distance_guide(fresh_aim(), self.depth.stats, calib.CAMERA.mode,
                                               calib.CAMERA.law_estimate, now))
        progress = self.live["progress"].status(now) if self.scan_state == "scanning" else None
        if self.lost:
            wait = self.retry_at - now
            self.guide.configure(fg=BAD, text="Reconnecting to the scanner ..." if self.switching["busy"] or wait <= 0 else
                                 f"Scanner disconnected: plug it in or replug it. Next try in {wait:.0f} s")
        elif progress is not None:
            hint = ("Tracking lost · Model held. Return to the last aligned view, or choose Start separate piece."
                    if self.can_start_separate_piece(now) else progress["hint"])
            self.guide.configure(text=hint, fg=BAD if progress["tracking"].startswith("Lost") else MUTED_GUIDE)
        else:
            hint = self.check_countdown() if self.scan_state == "counting" else None
            if hint is None and aim_view["idle"] and time.monotonic() - aim_view["t"] <= AIM_FRESH_S:
                hint = aim_hint(aim_view["piece"], self.ir.stats, self.imu.state() if self.imu else None,
                                found_markers["on"])
            self.guide.configure(text=hint[0] if hint else "", fg=hint[1] if hint else MUTED_GUIDE)
        retry_shown = self.retry.winfo_ismapped()
        if self.lost and not self.switching["busy"] and not retry_shown:
            self.retry.pack(side="right", padx=(6, 0))
        elif retry_shown and (not self.lost or self.switching["busy"]):
            self.retry.pack_forget()
        self.check_mesh()
        self.check_scan()
        self.model_pane.tick(self.scan_state, scan_view["lost"], self.live["commands"])
        scan_view.update(active=self.scan_state in ("scanning", "pausing", "resuming"),
                         return_guide=self.refresh_return_guide())
        recorder = self.rec["recorder"]
        if recorder:
            self.message = (f"REC {recorder.count} sets, {recorder.megabytes:.0f} MB"
                            + (f", {recorder.imu_rows} IMU samples" if recorder.imu_rows else "")
                            + f", {self.rec['missed']} missed, {recorder.free:.0f} GB free")
        if self.scan_state == "scanning":  # recompute after consuming frame replies, even if geometry was unchanged
            self.note.configure(text=progress_line(self.live["progress"].status()))
        else:
            self.note.configure(text=self.message)
        for panel in self.panels:
            panel.refresh()
        if time.monotonic() - self.info_at >= INFO_EVERY_S:
            self.info_at = time.monotonic()
            self.refresh_info()
        now = time.monotonic()
        if self.scan_state == "scanning" and self.tick_at is not None:
            self.ticks.append(now - self.tick_at)  # (how often the window got to run: logged at Stop)
        self.tick_at = now
        self.root.after(33, self.tick)

    def refresh_return_guide(self):
        """Compare a frozen acknowledged frame with Depth; never save or log the image."""
        blocked = self.lost or self.switching["busy"]
        if blocked or self.scan_state not in ACTIVE_SCAN_STATES:
            self.tracked_reference.clear()
        shown = self.tracked_reference.image() if self.scan_state == "scanning" and not blocked else None
        if shown is None:
            if self.return_photo is not None:
                self.return_panel.pack_forget()
                self.return_image.configure(image="")
                self.return_photo, self.shown_return = None, None
            return False
        if self.shown_return is not shown:
            image, cm, outlined = shown
            self.return_photo = tk.PhotoImage(data=image, format="PPM")
            self.shown_return = shown
            self.return_image.configure(image=self.return_photo)
            matched = "Match the cyan outline" if outlined else "Match the visible shape"
            distance = "" if cm is None else f" Centre was ~{cm:.1f} cm."
            self.return_note.configure(text=f"{matched} and angle in live Depth.{distance} Move slowly.")
            self.return_panel.pack(fill="x", after=self.model_pane, pady=(4, 0))
        return True

    def refresh_info(self):
        """The Status box (rewritten when it changed) and the Messages box (new lines added; it follows
        them unless it was scrolled back)."""
        status, messages = self.info["Status"], self.info["Messages"]
        text = "\n".join(self.status_lines())
        if text != status.get("1.0", "end-1c"):
            status.configure(state="normal")
            status.delete("1.0", "end")
            status.insert("1.0", text, "hang")
            status.configure(state="disabled")
        fresh = [(n, line) for n, line in list(self.messages) if n > self.messages_shown]
        if fresh:
            self.messages_shown = fresh[-1][0]
            at_end = messages.yview()[1] > 0.999
            messages.configure(state="normal")
            empty = messages.compare("end-1c", "==", "1.0")
            messages.insert("end", ("" if empty else "\n") + "\n".join(line for _, line in fresh), "hang")
            surplus = int(messages.index("end-1c").split(".")[0]) - MESSAGES_KEPT
            if surplus > 0:
                messages.delete("1.0", f"{surplus + 1}.0")
            messages.configure(state="disabled")
            if at_end:
                messages.see("end")

    def slider(self, parent, label, node, cid):
        """A Scale for one control (see SLIDERS), or None if the node does not have it."""
        info = next((c for c in node_controls(self.targets[node]).values() if c["id"] == cid), None)
        if info is None:
            return None

        def set_value(value):
            value = int(float(value))
            if self.synced.get(label) == value:  # (the scale showing the scanner's own value: nothing to send)
                return
            self.synced.pop(label, None)
            if label in IR_LABELS and self.ir_auto:
                self.set_ir_auto(False)  # moving an IR slider takes the IR exposure over by hand
            try:
                set_control(self.targets[node], cid, value)
            except (OSError, AttributeError) as e:
                self.log(f"{label}: {getattr(e, 'strerror', None) or e}")

        scale = tk.Scale(parent, label=label, orient="horizontal", from_=info["min"], to=info["max"],
                         resolution=info["step"] or 1, command=set_value, bg=panels.SURFACE, fg=panels.INK,
                         highlightbackground=panels.SURFACE, highlightcolor=panels.ACCENT, bd=0,
                         troughcolor=panels.LINE, activebackground=panels.HOVER, sliderrelief="flat")
        scale.set(info["value"])
        self.synced[label] = held(scale)  # (Tk calls set_value later, when drawn: it must not count as a move)
        scale.pack(fill="x")
        self.controls[label] = (node, cid, info, scale)
        return scale


def refuse(text):
    print(text, flush=True)
    root = tk.Tk()
    root.withdraw()
    tkinter.messagebox.showerror("triBUUTSCAN", text)
    root.destroy()


def open_app(root, ask=tkinter.messagebox.askretrycancel):
    """The App, or None if the user gives up: while the scanner can't be opened, say why and offer Retry."""
    while True:
        try:
            return App(root)
        except (RuntimeError, OSError) as e:
            print(f"scanner not available: {e}", flush=True)
            if not ask("triBUUTSCAN", scanner_problem(e), parent=root):
                return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="triBUUTSCAN live USB scanner viewer")
    parser.add_argument(gpu.FORCE_CPU, action="store_true", help="track and mesh on the CPU, even with a GPU")
    args = parser.parse_args(argv)
    gpu.force_cpu([gpu.FORCE_CPU] if args.force_cpu else [])  # worker and mesh jobs inherit it
    LOCK.parent.mkdir(exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released by the system when this process ends
    except BlockingIOError:
        refuse("The viewer is already running: use that window, or close it first.")
        return
    root = tk.Tk()
    root.withdraw()  # only the Retry question until the scanner opens
    app = open_app(root)
    if app is None:
        root.destroy()
        return
    root.deiconify()
    app.build_window()
    app.start()
    root.mainloop()


if __name__ == "__main__":
    main()
