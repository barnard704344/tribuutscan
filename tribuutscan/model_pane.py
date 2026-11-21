"""Native, bounded live-model viewport with no browser dependency."""
import math
import tkinter as tk

from . import panels



class ModelState:
    """Keep one sampled piece and a tagged worker subscription; Tk owns this state."""
    def __init__(self, renderer):
        self.renderer = renderer
        self.generation = 0
        self.reset(worker_gone=True)

    def reset(self, *, worker_gone=False):
        self.renderer.reset_frame()
        self.generation += 1
        self.subscribed = False
        self.piece = self.pieces = 1
        self.version = self.geometry = self.expected = self.first_serial = None
        self.yaw, self.pitch, self.zoom = 0.4, -0.25, 1.0

    def subscribe(self, commands):
        if not self.subscribed:
            commands.put(("coverage_subscribe", self.generation, self.piece))
            self.subscribed = True

    def choose(self, piece):
        if 1 <= piece <= self.pieces and piece != self.piece:
            self.piece = piece
            self.renderer.reset_frame()
            self.version = self.geometry = self.expected = self.first_serial = None
            self.generation += 1
            self.subscribed = False

    def receive(self, generation, piece, version, xyz_mm, seen):
        if not self.subscribed or generation != self.generation or piece != self.piece or version == self.version:
            return
        self.version = version
        self.geometry = (xyz_mm, seen) if len(xyz_mm) else None
        self.redraw()

    def redraw(self):
        self.expected = (None if self.geometry is None else
                         self.renderer.offer(*self.geometry, self.yaw, self.pitch, self.zoom))
        if self.expected is None or self.first_serial is None:
            self.first_serial = self.expected

    def rotate(self, dx, dy):
        self.yaw += dx * 0.012
        self.pitch = max(-math.pi / 2, min(math.pi / 2, self.pitch + dy * 0.012))
        self.redraw()

    def scale(self, factor):
        self.zoom = max(0.4, min(4.0, self.zoom * factor))
        self.redraw()


class ModelPane(tk.Frame):
    """Live model in the same window as Colour, IR and Depth."""
    def __init__(self, parent, *, width, height, renderer=None):
        super().__init__(parent, bg=panels.SURFACE, bd=0)
        if renderer is None:
            from .coverage_preview import PreviewRenderer
            renderer = PreviewRenderer(width, height)
        self.state = ModelState(renderer)
        self.drag = self.shown = None
        top = panels.frame(self)
        top.pack(fill="x", padx=8, pady=(3, 0))
        tk.Label(top, text="Live 3D model", bg=panels.SURFACE, fg=panels.INK,
                 font=panels.BOLD).pack(side="left")
        self.piece_name = tk.StringVar(value="Piece 1")
        self.piece_menu = panels.menu(top, self.piece_name, ("Piece 1",), self.select_piece)
        self.piece_menu.pack(side="right")
        legend = panels.frame(self)
        legend.pack(fill="x", padx=8, pady=(2, 3))
        for label, colour in (("Amber: 1–2", "#ffc45c"), ("Blue: 3+ observations", "#53b9ff")):
            tk.Label(legend, text=label, bg=colour, fg=panels.INK, font=panels.BOLD,
                     padx=5, pady=2).pack(side="left", padx=(0, 4))
        self.piece_count = 1
        self.blank = tk.PhotoImage(width=width, height=height)
        self.canvas = tk.Label(self, image=self.blank, bg="#101928", bd=0, highlightthickness=0)
        self.canvas.pack()
        self.photo = self.blank
        self.caption = tk.Label(self, bg=panels.SURFACE, fg=panels.MUTED, anchor="w", wraplength=width,
                                justify="left")
        self.caption.pack(fill="x", padx=8, pady=(2, 5))
        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<B1-Motion>", self.move_drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _: self.end_drag())
        self.canvas.bind("<MouseWheel>", lambda event: self.state.scale(1.12 if event.delta > 0 else 1 / 1.12))
        self.canvas.bind("<Button-4>", lambda _: self.state.scale(1.12))
        self.canvas.bind("<Button-5>", lambda _: self.state.scale(1 / 1.12))
        self.canvas.bind("<Double-Button-1>", lambda _: self.reset_orbit())
        self.shown_caption = None
        self.update_caption("idle", False)

    def reset(self, *, worker_gone=False):
        self.state.reset(worker_gone=worker_gone)
        self.set_pieces(1)
        self.piece_name.set("Piece 1")
        self.render_latest()

    def set_pieces(self, count):
        if count != self.piece_count:
            self.state.pieces = self.piece_count = count
            names = [f"Piece {part}" for part in range(1, count + 1)]
            self.piece_menu.set_menu(f"Piece {self.state.piece}", *names)

    def select_piece(self, name):
        self.state.choose(int(name.split()[-1]))
        self.render_latest()

    def reset_orbit(self):
        self.state.yaw, self.state.pitch, self.state.zoom = 0.4, -0.25, 1.0
        self.state.redraw()

    def start_drag(self, event):
        self.drag = (event.x, event.y)

    def move_drag(self, event):
        if self.drag is not None:
            self.state.rotate(event.x - self.drag[0], event.y - self.drag[1])
            self.drag = (event.x, event.y)

    def end_drag(self):
        self.drag = None

    def render_latest(self):
        expected = self.state.expected
        latest = self.state.renderer.latest
        if expected is None:
            if self.shown is not None:
                self.canvas.configure(image=self.blank)
                self.photo, self.shown = self.blank, None
        elif (latest is not None and self.state.first_serial <= latest[0] <= expected
              and (self.shown is None or latest[0] > self.shown)):
            self.photo = tk.PhotoImage(data=latest[1], format="PPM")
            self.canvas.configure(image=self.photo)
            self.shown = latest[0]

    def update_caption(self, phase, lost):
        caption = ("Start a scan to see the model build here." if self.state.geometry is None and phase == "idle" else
                   "Waiting for this piece to have points..." if self.state.geometry is None else
                   "Paused · Inspect the held model." if phase == "paused" else
                   "Tracking lost · Model held." if lost else
                   "Last live model." if phase not in ("scanning", "pausing", "resuming") else
                   "Live · Drag to rotate, scroll to zoom.")
        if self.state.geometry is not None:
            caption += " Sampled model; not an accuracy check."
        if caption != self.shown_caption:
            self.caption.configure(text=caption)
            self.shown_caption = caption

    def tick(self, phase, lost, commands):
        if commands is not None and phase in ("scanning", "pausing", "paused", "resuming"):
            self.state.subscribe(commands)
        self.render_latest()
        self.update_caption(phase, lost)

    def close(self):
        self.state.renderer.close()
