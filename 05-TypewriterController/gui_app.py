from __future__ import annotations

import queue
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from config_loader import load_config
from models import (
    CMD_NEW_LINE,
    CMD_PRINT_BLUE,
    CMD_PRINT_GREEN,
    CMD_SPACE,
    GridModel,
    correction_print_cmd,
    direct_print_cmd,
    RuntimePlan,
    Step,
    compile_runtime_plan,
    compile_fill_runtime_plan,
    export_models_to_json,
    format_hms,
    parse_json_to_models,
    normalize_ribbon_color,
    rle_compile_grid_to_steps,
)
from servo_rig import ServoRig, HAS_BONNET


class App(tk.Tk):
    def __init__(self, config_path: str = "typewriter_config.txt") -> None:
        super().__init__()

        self.title("Typewriter Map Controller")
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        window_width = max(320, min(1200, screen_width - 40))
        window_height = max(320, min(760, screen_height - 80))
        self.geometry(f"{window_width}x{window_height}")
        self.minsize(min(700, window_width), min(400, window_height))

        self.msg_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()

        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.run_event = threading.Event()
        self.run_event.set()
        self.is_running = False

        self.loaded_path: Optional[str] = None
        self.loaded_json: Dict[str, Any] = {}
        self.grid_model: Optional[GridModel] = None
        self.steps: List[Step] = []

        self.current_plan: Optional[RuntimePlan] = None
        self.fill_plan: Optional[RuntimePlan] = None
        self.fill_steps: List[Step] = []
        self.fill_count: int = 0
        self.execution_mode = "run"
        self.done_expanded_count: int = 0
        self.performance_totals: Dict[str, float] = {}
        self.performance_counts: Dict[str, int] = {}
        self.performance_line_samples: int = 0

        self.preview_printed: List[List[bool]] = []
        self.preview_rects: List[List[int]] = []
        self.active_cell: Optional[Tuple[int, int]] = None
        self.preview_grid_model: Optional[GridModel] = None

        self.fill_preview_printed: List[List[bool]] = []
        self.fill_preview_rects: List[List[int]] = []
        self.fill_active_cell: Optional[Tuple[int, int]] = None
        self.fill_preview_grid_model: Optional[GridModel] = None
        self.fill_done_expanded_count: int = 0

        # Load config from .txt (JSON)
        self.cfg, cfg_status = load_config(config_path)
        self.config_path = config_path


        self._build_style()
        self._build_ui()

        self.status_var.set(cfg_status)

        self.after(60, self._poll_queue)
        self.after(200, self._update_time_estimates_tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TButton", padding=8)
        style.configure("TLabel", padding=4)
        style.configure("Header.TLabel", font=("TkDefaultFont", 12, "bold"))
        style.configure("Treeview", rowheight=26)

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=12)
        top.pack(fill="both", expand=True)

        header = ttk.Frame(top)
        header.pack(fill="x")

        self.status_var = tk.StringVar(value="Load a JSON file to begin.")
        ttk.Label(header, text="Typewriter Map Controller", style="Header.TLabel").pack(side="left")
        ttk.Label(header, textvariable=self.status_var).pack(side="right")

        self.nb = ttk.Notebook(top)
        self.nb.pack(fill="both", expand=True, pady=(10, 0))

        self.tab_run = ttk.Frame(self.nb, padding=12)
        self.tab_fill = ttk.Frame(self.nb, padding=12)
        self.tab_edit = ttk.Frame(self.nb, padding=12)
        self.tab_settings = ttk.Frame(self.nb, padding=12)

        self.nb.add(self.tab_run, text="Run")
        self.nb.add(self.tab_fill, text="Fill")
        self.nb.add(self.tab_edit, text="Edit")
        self.nb.add(self.tab_settings, text="Settings")

        self._build_run_tab()
        self._build_fill_tab()
        self._build_edit_tab()
        self._build_settings_tab()

    # -------- Run tab --------
    def _build_run_tab(self) -> None:
        row1 = ttk.Frame(self.tab_run)
        row1.pack(fill="x")

        ttk.Button(row1, text="Load JSON…", command=self.on_load_json).pack(side="left")
        ttk.Button(row1, text="Save JSON As…", command=self.on_save_json, state="disabled").pack(side="left", padx=(8, 0))
        self.btn_save = row1.winfo_children()[1]

        ctrl = ttk.Frame(row1)
        ctrl.pack(side="right")

        self.btn_start = ttk.Button(ctrl, text="Start", command=self.on_start, state="disabled")
        self.btn_pause = ttk.Button(ctrl, text="Pause", command=self.on_pause, state="disabled")
        self.btn_stop = ttk.Button(ctrl, text="Stop", command=self.on_stop, state="disabled")

        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_pause.pack(side="left", padx=(0, 8))
        self.btn_stop.pack(side="left")

        mono = ttk.Labelframe(self.tab_run, text="Monochrome (skip OFF cells with SPACE)", padding=10)
        mono.pack(fill="x", pady=(12, 8))

        self.mono_enabled = tk.BooleanVar(value=bool(self.cfg["mode"]["monochrome_enabled"]))
        self.mono_color = tk.StringVar(value=str(self.cfg["mode"]["monochrome_color"]))

        ttk.Checkbutton(mono, text="Enable monochrome", variable=self.mono_enabled, command=self._sync_mono).pack(side="left")
        ttk.Label(mono, text="Color:").pack(side="left", padx=(12, 6))
        ttk.Combobox(mono, textvariable=self.mono_color, values=["BLUE", "GREEN"], width=8, state="readonly").pack(side="left")
        ttk.Button(mono, text="Apply", command=self._sync_mono).pack(side="left", padx=(8, 0))

        prog = ttk.Frame(self.tab_run)
        prog.pack(fill="x", pady=(6, 8))

        self.progress = ttk.Progressbar(prog, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x")

        self.progress_text_var = tk.StringVar(value="Ready.")
        ttk.Label(prog, textvariable=self.progress_text_var).pack(anchor="w", pady=(4, 0))

        ttk.Button(prog, text="Reset spacebar to rest", command=self.on_reset_spacebar).pack(anchor="e", pady=(4, 0))

        metrics = ttk.Labelframe(self.tab_run, text="Counts (estimated from instructions)", padding=10)
        metrics.pack(fill="x", pady=(8, 10))

        self.metrics_var = tk.StringVar(value="")
        ttk.Label(metrics, textvariable=self.metrics_var, justify="left").pack(anchor="w")

        paned = ttk.Panedwindow(self.tab_run, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=(0, 0))

        self.preview_frame = ttk.Labelframe(paned, text="Map preview (greyed after printed)", padding=10)
        paned.add(self.preview_frame, weight=2)

        self.preview_canvas = tk.Canvas(self.preview_frame, highlightthickness=0)
        self.preview_scroll_y = ttk.Scrollbar(self.preview_frame, orient="vertical", command=self.preview_canvas.yview)
        self.preview_scroll_x = ttk.Scrollbar(self.preview_frame, orient="horizontal", command=self.preview_canvas.xview)
        self.preview_canvas.configure(yscrollcommand=self.preview_scroll_y.set, xscrollcommand=self.preview_scroll_x.set)
        self.preview_canvas.bind("<Configure>", self._on_preview_canvas_configure)

        self.preview_scroll_y.pack(side="right", fill="y")
        self.preview_scroll_x.pack(side="bottom", fill="x")
        self.preview_canvas.pack(side="left", fill="both", expand=True)

        steps_frame = ttk.Labelframe(paned, text="Instruction steps", padding=10)
        paned.add(steps_frame, weight=3)

        cols = ("idx", "cmd", "count", "state")
        self.tree = ttk.Treeview(steps_frame, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("idx", text="#")
        self.tree.heading("cmd", text="Command")
        self.tree.heading("count", text="Count")
        self.tree.heading("state", text="State")

        self.tree.column("idx", width=60, anchor="e")
        self.tree.column("cmd", width=220, anchor="w")
        self.tree.column("count", width=100, anchor="e")
        self.tree.column("state", width=120, anchor="w")

        scroll = ttk.Scrollbar(steps_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.tag_configure("done", foreground="#777777")
        self.tree.tag_configure("active", background="#e7f0ff")

    # -------- Fill tab --------
    def _build_fill_tab(self) -> None:
        row1 = ttk.Frame(self.tab_fill)
        row1.pack(fill="x")

        ttk.Button(row1, text="Load JSON...", command=self.on_load_json).pack(side="left")
        self.fill_file_var = tk.StringVar(value="Uses the same JSON file as Run.")
        ttk.Label(row1, textvariable=self.fill_file_var).pack(side="left", padx=(10, 0))

        ctrl = ttk.Frame(row1)
        ctrl.pack(side="right")
        self.fill_btn_start = ttk.Button(ctrl, text="Start Fill", command=self.on_fill_start, state="disabled")
        self.fill_btn_pause = ttk.Button(ctrl, text="Pause", command=self.on_fill_pause, state="disabled")
        self.fill_btn_stop = ttk.Button(ctrl, text="Stop", command=self.on_fill_stop, state="disabled")
        self.fill_btn_start.pack(side="left", padx=(0, 8))
        self.fill_btn_pause.pack(side="left", padx=(0, 8))
        self.fill_btn_stop.pack(side="left")

        info = (
            "Return the carriage to the original top-left start, install the third-color ribbon, then start Fill. "
            "SPACE cells print with the primary ribbon position; existing colored cells are skipped."
        )
        ttk.Label(self.tab_fill, text=info, wraplength=1050).pack(fill="x", pady=(12, 8))

        prog = ttk.Frame(self.tab_fill)
        prog.pack(fill="x", pady=(6, 8))
        self.fill_progress = ttk.Progressbar(prog, orient="horizontal", mode="determinate")
        self.fill_progress.pack(fill="x")
        self.fill_progress_text_var = tk.StringVar(value="Load a JSON file with SPACE cells.")
        ttk.Label(prog, textvariable=self.fill_progress_text_var).pack(anchor="w", pady=(4, 0))
        ttk.Button(prog, text="Reset spacebar to rest", command=self.on_reset_spacebar).pack(anchor="e", pady=(4, 0))

        metrics = ttk.Labelframe(self.tab_fill, text="Fill counts (estimated from instructions)", padding=10)
        metrics.pack(fill="x", pady=(8, 10))
        self.fill_metrics_var = tk.StringVar(value="")
        ttk.Label(metrics, textvariable=self.fill_metrics_var, justify="left").pack(anchor="w")

        paned = ttk.Panedwindow(self.tab_fill, orient="horizontal")
        paned.pack(fill="both", expand=True)

        preview_frame = ttk.Labelframe(paned, text="Fill preview (third color shown in black)", padding=10)
        paned.add(preview_frame, weight=2)
        self.fill_preview_canvas = tk.Canvas(preview_frame, highlightthickness=0)
        fill_scroll_y = ttk.Scrollbar(preview_frame, orient="vertical", command=self.fill_preview_canvas.yview)
        fill_scroll_x = ttk.Scrollbar(preview_frame, orient="horizontal", command=self.fill_preview_canvas.xview)
        self.fill_preview_canvas.configure(yscrollcommand=fill_scroll_y.set, xscrollcommand=fill_scroll_x.set)
        self.fill_preview_canvas.bind("<Configure>", self._on_fill_preview_canvas_configure)
        fill_scroll_y.pack(side="right", fill="y")
        fill_scroll_x.pack(side="bottom", fill="x")
        self.fill_preview_canvas.pack(side="left", fill="both", expand=True)

        steps_frame = ttk.Labelframe(paned, text="Optimized fill steps", padding=10)
        paned.add(steps_frame, weight=3)
        cols = ("idx", "cmd", "count", "state")
        self.fill_tree = ttk.Treeview(steps_frame, columns=cols, show="headings", selectmode="browse")
        for column, heading, width, anchor in (
            ("idx", "#", 60, "e"),
            ("cmd", "Command", 220, "w"),
            ("count", "Count", 100, "e"),
            ("state", "State", 120, "w"),
        ):
            self.fill_tree.heading(column, text=heading)
            self.fill_tree.column(column, width=width, anchor=anchor)
        fill_tree_scroll = ttk.Scrollbar(steps_frame, orient="vertical", command=self.fill_tree.yview)
        self.fill_tree.configure(yscrollcommand=fill_tree_scroll.set)
        self.fill_tree.pack(side="left", fill="both", expand=True)
        fill_tree_scroll.pack(side="right", fill="y")
        self.fill_tree.tag_configure("done", foreground="#777777")
        self.fill_tree.tag_configure("active", background="#e7f0ff")

    def _rebuild_fill_plan(self) -> None:
        if not hasattr(self, "fill_tree"):
            return
        self.fill_tree.delete(*self.fill_tree.get_children())
        if not self.steps:
            self.fill_plan = None
            self.fill_steps = []
            self.fill_count = 0
            self.fill_btn_start.configure(state="disabled")
            self.fill_progress.configure(value=0, maximum=1)
            self.fill_progress_text_var.set("Load a JSON file with SPACE cells.")
            self.fill_metrics_var.set("")
            self._clear_fill_preview_canvas()
            return

        try:
            fill_steps, plan, fill_count = compile_fill_runtime_plan(
                self.steps, self.cfg, self.loaded_json, self.grid_model
            )
        except Exception as e:
            self.fill_plan = None
            self.fill_steps = []
            self.fill_count = 0
            self.fill_btn_start.configure(state="disabled")
            self.fill_metrics_var.set(f"Could not compile fill plan.\n{e}")
            self._clear_fill_preview_canvas()
            return

        self.fill_steps = fill_steps
        self.fill_plan = plan
        self.fill_count = fill_count
        self.fill_done_expanded_count = 0
        for i, step in enumerate(fill_steps):
            self.fill_tree.insert("", "end", values=(i + 1, step.cmd, step.count, ""))
        self.fill_progress.configure(value=0, maximum=max(1, len(plan.expanded_cmds)))
        self.fill_btn_start.configure(state="normal" if fill_count > 0 and not self.is_running else "disabled")
        self._draw_fill_preview_grid(plan.preview_grid)
        self._refresh_fill_metrics_text()
        self._update_fill_progress_text()

    def _refresh_fill_metrics_text(self) -> None:
        plan = self.fill_plan
        if not plan:
            self.fill_metrics_var.set("")
            return
        direct_color = "GREEN" if direct_print_cmd(self.cfg) == CMD_PRINT_GREEN else "BLUE"
        if self.fill_count == 0:
            self.fill_metrics_var.set("No SPACE cells were found. There is nothing to fill.")
            return
        self.fill_metrics_var.set(
            f"Third-color characters: {self.fill_count} (shown as black)  "
            f"Primary ribbon position: {direct_color}\n"
            f"Movement: SPACEBAR={plan.actions['SPACES']}  RETURN={plan.actions['NEW_LINES']}  "
            f"Estimated total time: {format_hms(plan.total_est_s)}"
        )

    def _clear_fill_preview_canvas(self) -> None:
        self.fill_preview_canvas.delete("all")
        self.fill_preview_printed = []
        self.fill_preview_rects = []
        self.fill_active_cell = None
        self.fill_preview_grid_model = None

    def _on_fill_preview_canvas_configure(self, _event: tk.Event) -> None:
        if self.fill_preview_grid_model:
            self._draw_fill_preview_grid(self.fill_preview_grid_model, preserve_printed=True)

    def _draw_fill_preview_grid(self, grid: GridModel, preserve_printed: bool = False) -> None:
        previous_printed = self.fill_preview_printed if preserve_printed else []
        previous_active = self.fill_active_cell if preserve_printed else None
        self.fill_preview_canvas.delete("all")
        self.fill_preview_grid_model = grid
        rows, cols = grid.rows, grid.cols
        if preserve_printed and len(previous_printed) == rows and all(len(row) == cols for row in previous_printed):
            self.fill_preview_printed = [row[:] for row in previous_printed]
        else:
            self.fill_preview_printed = [[False for _ in range(cols)] for _ in range(rows)]
        self.fill_preview_rects = [[-1 for _ in range(cols)] for _ in range(rows)]
        self.fill_active_cell = previous_active

        canvas_w = max(1, self.fill_preview_canvas.winfo_width())
        canvas_h = max(1, self.fill_preview_canvas.winfo_height())
        margin = 8
        cell = max(1.0, min((canvas_w - 2 * margin) / max(1, cols), (canvas_h - 2 * margin) / max(1, rows)))
        grid_w = cell * cols
        grid_h = cell * rows
        x_start = max(margin, (canvas_w - grid_w) / 2)
        y_start = max(margin, (canvas_h - grid_h) / 2)
        outline = "#7a7a7a" if cell >= 4 else ""
        outline_width = 1 if cell >= 4 else 0
        self.fill_preview_canvas.configure(scrollregion=(0, 0, canvas_w, canvas_h))

        for r in range(rows):
            for c in range(cols):
                x0 = x_start + c * cell
                y0 = y_start + r * cell
                x1 = x_start + (c + 1) * cell
                y1 = y_start + (r + 1) * cell
                is_fill = grid.cells[r][c] == 3
                fill = "#777777" if is_fill and self.fill_preview_printed[r][c] else "#111111" if is_fill else "#f7f7f7"
                rect = self.fill_preview_canvas.create_rectangle(
                    x0, y0, x1, y1, fill=fill, outline=outline, width=outline_width
                )
                self.fill_preview_rects[r][c] = rect

        if self.fill_active_cell is not None:
            self._set_fill_active_cell_outline(self.fill_active_cell)

    def _set_fill_active_cell_outline(self, new_cell: Optional[Tuple[int, int]]) -> None:
        plan = self.fill_plan
        if not plan:
            return
        if self.fill_active_cell is not None:
            r0, c0 = self.fill_active_cell
            if 0 <= r0 < plan.preview_grid.rows and 0 <= c0 < plan.preview_grid.cols:
                self.fill_preview_canvas.itemconfigure(self.fill_preview_rects[r0][c0], outline="#7a7a7a", width=1)
        self.fill_active_cell = new_cell
        if new_cell is not None:
            r1, c1 = new_cell
            if 0 <= r1 < plan.preview_grid.rows and 0 <= c1 < plan.preview_grid.cols:
                self.fill_preview_canvas.itemconfigure(self.fill_preview_rects[r1][c1], outline="#d00000", width=2)

    def _update_fill_progress_text(self) -> None:
        plan = self.fill_plan
        if not plan:
            self.fill_progress_text_var.set("Load a JSON file with SPACE cells.")
            return
        total = max(1, len(plan.expanded_cmds))
        done = max(0, min(self.fill_done_expanded_count, total))
        remaining = max(0.0, plan.total_est_s - plan.prefix_s[min(done, len(plan.prefix_s) - 1)])
        if self.fill_count == 0:
            self.fill_progress_text_var.set("No SPACE cells found; Fill is disabled.")
        else:
            self.fill_progress_text_var.set(
                f"{(done / total) * 100:5.1f}% complete.  Estimated remaining: {format_hms(remaining)}  "
                f"Estimated total: {format_hms(plan.total_est_s)}"
            )

    def on_fill_start(self) -> None:
        if self.is_running:
            return
        self._rebuild_fill_plan()
        if not self.fill_plan or self.fill_count == 0:
            messagebox.showinfo("Nothing to fill", "This JSON file does not contain any SPACE cells.")
            return
        if not HAS_BONNET:
            messagebox.showwarning(
                "Bonnet not available",
                "This system cannot access the Adafruit Servo Bonnet. Run on a Raspberry Pi with adafruit_servokit installed.",
            )
            return

        self.execution_mode = "fill"
        self.fill_done_expanded_count = 0
        self.fill_progress.configure(value=0, maximum=max(1, len(self.fill_plan.expanded_cmds)))
        self._draw_fill_preview_grid(self.fill_plan.preview_grid)
        self._update_fill_progress_text()
        self.stop_event.clear()
        self.run_event.set()
        self.is_running = True
        self.fill_btn_start.configure(state="disabled")
        self.fill_btn_pause.configure(state="normal", text="Pause")
        self.fill_btn_stop.configure(state="normal")
        self.btn_start.configure(state="disabled")
        self.worker_thread = threading.Thread(target=self._worker_run, args=(self.fill_plan,), daemon=True)
        self.worker_thread.start()

    def on_fill_pause(self) -> None:
        if not self.is_running or self.execution_mode != "fill":
            return
        if self.run_event.is_set():
            self.run_event.clear()
            self.fill_btn_pause.configure(text="Resume")
            self.status_var.set("Fill paused.")
        else:
            self.run_event.set()
            self.fill_btn_pause.configure(text="Pause")
            self.status_var.set("Fill running.")

    def on_fill_stop(self) -> None:
        if not self.is_running or self.execution_mode != "fill":
            return
        self.stop_event.set()
        self.run_event.set()
        self.status_var.set("Stopping fill...")

    def _sync_mono(self) -> None:
        self.cfg["mode"]["monochrome_enabled"] = bool(self.mono_enabled.get())
        self.cfg["mode"]["monochrome_color"] = str(self.mono_color.get())

        if self.grid_model:
            if self.cfg["mode"]["monochrome_enabled"]:
                chosen = 1 if self.cfg["mode"]["monochrome_color"] == "BLUE" else 2
                for r in range(self.grid_model.rows):
                    for c in range(self.grid_model.cols):
                        self.grid_model.cells[r][c] = chosen if self.grid_model.cells[r][c] != 0 else 0
                self.steps = rle_compile_grid_to_steps(self.grid_model)
                self._refresh_steps_view()
                self._rebuild_grid_editor()

        self._rebuild_preview_and_counts()
        self._rebuild_fill_plan()

    # -------- Edit tab --------
    def _build_edit_tab(self) -> None:
        self.edit_container = ttk.Frame(self.tab_edit)
        self.edit_container.pack(fill="both", expand=True)
        self._render_edit_tab()

    def _clear_edit_container(self) -> None:
        for child in self.edit_container.winfo_children():
            child.destroy()

    def _render_edit_tab(self) -> None:
        self._clear_edit_container()

        if not self.steps:
            ttk.Label(self.edit_container, text="Load a JSON file to edit instructions.").pack(anchor="w")
            return

        if self.grid_model:
            self._build_grid_editor(self.edit_container)
        else:
            self._build_step_editor(self.edit_container)

    def _build_step_editor(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Step editor (legacy sequence).", style="Header.TLabel").pack(anchor="w", pady=(0, 10))

        hint = (
            "Tip: This editor works best if your JSON includes SPACE steps. "
            "If you want per-cell edits, export with a grid from your generator."
        )
        ttk.Label(parent, text=hint, wraplength=900).pack(anchor="w", pady=(0, 12))

        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)

        cols = ("cmd", "count")
        self.edit_tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        self.edit_tree.heading("cmd", text="Command")
        self.edit_tree.heading("count", text="Count")
        self.edit_tree.column("cmd", width=260, anchor="w")
        self.edit_tree.column("count", width=120, anchor="e")

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.edit_tree.yview)
        self.edit_tree.configure(yscrollcommand=scroll.set)
        self.edit_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        btns = ttk.Frame(parent)
        btns.pack(fill="x", pady=(12, 0))

        ttk.Button(btns, text="Add BLUE", command=lambda: self._add_step(CMD_PRINT_BLUE)).pack(side="left")
        ttk.Button(btns, text="Add GREEN", command=lambda: self._add_step(CMD_PRINT_GREEN)).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Add SPACE", command=lambda: self._add_step(CMD_SPACE)).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Add NEW_LINE", command=lambda: self._add_step(CMD_NEW_LINE)).pack(side="left", padx=(8, 0))

        ttk.Button(btns, text="Delete", command=self._delete_step).pack(side="right")
        ttk.Button(btns, text="Move Down", command=lambda: self._move_step(+1)).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Move Up", command=lambda: self._move_step(-1)).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Edit Selected…", command=self._edit_selected_step).pack(side="right", padx=(8, 0))

        self._refresh_step_editor()

    def _refresh_step_editor(self) -> None:
        if not hasattr(self, "edit_tree"):
            return
        self.edit_tree.delete(*self.edit_tree.get_children())
        for s in self.steps:
            self.edit_tree.insert("", "end", values=(s.cmd, s.count))

    def _selected_step_index(self) -> Optional[int]:
        if not hasattr(self, "edit_tree"):
            return None
        sel = self.edit_tree.selection()
        if not sel:
            return None
        item = sel[0]
        idx = self.edit_tree.index(item)
        if 0 <= idx < len(self.steps):
            return idx
        return None

    def _add_step(self, cmd: str) -> None:
        if self.is_running:
            messagebox.showinfo("Running", "Stop the job before editing steps.")
            return
        self.steps.append(Step(cmd, 1))
        self._refresh_step_editor()
        self._refresh_steps_view()
        self._rebuild_preview_and_counts()
        self._rebuild_fill_plan()

    def _delete_step(self) -> None:
        if self.is_running:
            messagebox.showinfo("Running", "Stop the job before editing steps.")
            return
        idx = self._selected_step_index()
        if idx is None:
            return
        self.steps.pop(idx)
        self._refresh_step_editor()
        self._refresh_steps_view()
        self._rebuild_preview_and_counts()
        self._rebuild_fill_plan()

    def _move_step(self, delta: int) -> None:
        if self.is_running:
            messagebox.showinfo("Running", "Stop the job before editing steps.")
            return
        idx = self._selected_step_index()
        if idx is None:
            return
        j = idx + delta
        if not (0 <= j < len(self.steps)):
            return
        self.steps[idx], self.steps[j] = self.steps[j], self.steps[idx]
        self._refresh_step_editor()
        self._refresh_steps_view()
        self._rebuild_preview_and_counts()
        self._rebuild_fill_plan()
        item = self.edit_tree.get_children()[j]
        self.edit_tree.selection_set(item)
        self.edit_tree.see(item)

    def _edit_selected_step(self) -> None:
        if self.is_running:
            messagebox.showinfo("Running", "Stop the job before editing steps.")
            return
        idx = self._selected_step_index()
        if idx is None:
            return

        s = self.steps[idx]
        dialog = tk.Toplevel(self)
        dialog.title("Edit step")
        dialog.transient(self)
        dialog.grab_set()

        cmd_var = tk.StringVar(value=s.cmd)
        count_var = tk.StringVar(value=str(s.count))

        ttk.Label(dialog, text="Command:").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        cmb = ttk.Combobox(
            dialog,
            textvariable=cmd_var,
            state="readonly",
            values=[CMD_PRINT_BLUE, CMD_PRINT_GREEN, CMD_SPACE, CMD_NEW_LINE],
            width=18,
        )
        cmb.grid(row=0, column=1, sticky="ew", padx=10, pady=8)

        ttk.Label(dialog, text="Count:").grid(row=1, column=0, sticky="w", padx=10, pady=8)
        ent = ttk.Entry(dialog, textvariable=count_var, width=10)
        ent.grid(row=1, column=1, sticky="w", padx=10, pady=8)

        def save() -> None:
            try:
                c = int(count_var.get())
                if c < 1:
                    raise ValueError
            except Exception:
                messagebox.showerror("Invalid", "Count must be an integer >= 1.")
                return
            self.steps[idx] = Step(cmd=str(cmd_var.get()), count=c)
            self._refresh_step_editor()
            self._refresh_steps_view()
            self._rebuild_preview_and_counts()
            self._rebuild_fill_plan()
            dialog.destroy()

        btns = ttk.Frame(dialog)
        btns.grid(row=2, column=0, columnspan=2, sticky="e", padx=10, pady=10)
        ttk.Button(btns, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(btns, text="Save", command=save).pack(side="right", padx=(0, 8))

        dialog.columnconfigure(1, weight=1)
        ent.focus_set()

    def _build_grid_editor(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Cell editor (OFF → BLUE → GREEN)", style="Header.TLabel").pack(anchor="w", pady=(0, 10))

        help_text = (
            "Click a cell to cycle OFF, BLUE, GREEN.\n"
            "Tip: Use Monochrome in the Run tab to force all ON cells to BLUE or GREEN while keeping OFF as skips."
        )
        ttk.Label(parent, text=help_text).pack(anchor="w", pady=(0, 12))

        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(0, 10))

        ttk.Button(toolbar, text="All OFF", command=lambda: self._grid_fill(0)).pack(side="left")
        ttk.Button(toolbar, text="All BLUE", command=lambda: self._grid_fill(1)).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="All GREEN", command=lambda: self._grid_fill(2)).pack(side="left", padx=(8, 0))

        ttk.Label(toolbar, text="  ").pack(side="left")

        ttk.Button(toolbar, text="Recompile Steps", command=self._recompile_from_grid).pack(side="left", padx=(8, 0))

        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)

        self.grid_canvas = tk.Canvas(outer, highlightthickness=0)
        self.grid_scroll_y = ttk.Scrollbar(outer, orient="vertical", command=self.grid_canvas.yview)
        self.grid_scroll_x = ttk.Scrollbar(outer, orient="horizontal", command=self.grid_canvas.xview)

        self.grid_canvas.configure(yscrollcommand=self.grid_scroll_y.set, xscrollcommand=self.grid_scroll_x.set)

        self.grid_scroll_y.pack(side="right", fill="y")
        self.grid_scroll_x.pack(side="bottom", fill="x")
        self.grid_canvas.pack(side="left", fill="both", expand=True)

        self.grid_inner = ttk.Frame(self.grid_canvas)
        self.grid_window = self.grid_canvas.create_window((0, 0), window=self.grid_inner, anchor="nw")

        def on_configure(event) -> None:
            self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all"))

        def on_canvas_configure(event) -> None:
            self.grid_canvas.itemconfig(self.grid_window, width=event.width)

        self.grid_inner.bind("<Configure>", on_configure)
        self.grid_canvas.bind("<Configure>", on_canvas_configure)

        self._rebuild_grid_editor()

    def _rebuild_grid_editor(self) -> None:
        if not self.grid_model or not hasattr(self, "grid_inner"):
            return

        for child in self.grid_inner.winfo_children():
            child.destroy()

        cell_w = 3
        pad = 1

        self._grid_btns: List[List[tk.Button]] = []
        for r in range(self.grid_model.rows):
            row_btns: List[tk.Button] = []
            for c in range(self.grid_model.cols):
                b = tk.Button(
                    self.grid_inner,
                    text=" ",
                    width=cell_w,
                    relief="ridge",
                    command=lambda rr=r, cc=c: self._toggle_cell(rr, cc),
                )
                b.grid(row=r, column=c, padx=pad, pady=pad, sticky="nsew")
                row_btns.append(b)
            self._grid_btns.append(row_btns)

        for c in range(self.grid_model.cols):
            self.grid_inner.grid_columnconfigure(c, weight=0)

        self._paint_grid()

    def _paint_grid(self) -> None:
        if not self.grid_model or not hasattr(self, "_grid_btns"):
            return
        for r in range(self.grid_model.rows):
            for c in range(self.grid_model.cols):
                v = self.grid_model.cells[r][c]
                b = self._grid_btns[r][c]
                if v == 0:
                    b.configure(bg="#f0f0f0", activebackground="#f0f0f0")
                elif v == 1:
                    b.configure(bg="#cfe3ff", activebackground="#cfe3ff")
                else:
                    b.configure(bg="#d6f5d6", activebackground="#d6f5d6")

    def _toggle_cell(self, r: int, c: int) -> None:
        if self.is_running:
            messagebox.showinfo("Running", "Stop the job before editing cells.")
            return
        if not self.grid_model:
            return
        self.grid_model.cells[r][c] = (self.grid_model.cells[r][c] + 1) % 3
        self._paint_grid()

    def _grid_fill(self, value: int) -> None:
        if self.is_running:
            messagebox.showinfo("Running", "Stop the job before editing cells.")
            return
        if not self.grid_model:
            return
        for r in range(self.grid_model.rows):
            for c in range(self.grid_model.cols):
                self.grid_model.cells[r][c] = value
        self._paint_grid()

    def _recompile_from_grid(self) -> None:
        if self.is_running:
            messagebox.showinfo("Running", "Stop the job before recompiling.")
            return
        if not self.grid_model:
            return
        self.grid_model.clamp()
        self.steps = rle_compile_grid_to_steps(self.grid_model)
        self._refresh_steps_view()
        self._rebuild_preview_and_counts()
        self._rebuild_fill_plan()
        self.status_var.set("Recompiled steps from grid.")

    # -------- Settings tab --------
    def _build_settings_tab(self) -> None:
        btns = ttk.Frame(self.tab_settings)
        btns.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(btns, text="Apply Settings", command=self.on_apply_settings).pack(side="left")
        ttk.Button(btns, text="Reload Config File", command=self.on_reload_config).pack(side="left", padx=(8, 0))

        scroll_area = ttk.Frame(self.tab_settings)
        scroll_area.pack(fill="both", expand=True)

        settings_canvas = tk.Canvas(scroll_area, highlightthickness=0)
        settings_scrollbar = ttk.Scrollbar(scroll_area, orient="vertical", command=settings_canvas.yview)
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        settings_scrollbar.pack(side="right", fill="y")
        settings_canvas.pack(side="left", fill="both", expand=True)

        settings_content = ttk.Frame(settings_canvas)
        settings_window = settings_canvas.create_window((0, 0), window=settings_content, anchor="nw")

        def update_scroll_region(_event: tk.Event) -> None:
            settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))

        def fit_content_width(event: tk.Event) -> None:
            settings_canvas.itemconfigure(settings_window, width=event.width)

        settings_content.bind("<Configure>", update_scroll_region)
        settings_canvas.bind("<Configure>", fit_content_width)

        ttk.Label(settings_content, text="Hardware settings", style="Header.TLabel").pack(anchor="w", pady=(0, 10))

        info = (
            "These settings update channels, angles, and timing for this session.\n"
            "Edit typewriter_config.txt for persistent changes.\n"
            "Stop the job before changing settings."
        )
        ttk.Label(settings_content, text=info).pack(anchor="w", pady=(0, 12))

        wrap = ttk.Frame(settings_content)
        wrap.pack(fill="both", expand=True)

        left = ttk.Labelframe(wrap, text="Channels (bonnet)", padding=10)
        mid = ttk.Labelframe(wrap, text="Angles (degrees)", padding=10)
        right = ttk.Labelframe(wrap, text="Timing (seconds)", padding=10)

        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        mid.pack(side="left", fill="both", expand=True, padx=(0, 10))
        right.pack(side="left", fill="both", expand=True)

        self.vars: Dict[str, tk.StringVar] = {}

        def add_field(parent: ttk.Frame, key: str, value: Any, row: int) -> None:
            self.vars[key] = tk.StringVar(value=str(value))
            ttk.Label(parent, text=key).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(parent, textvariable=self.vars[key], width=12).grid(row=row, column=1, sticky="w", pady=4)

        channels = self.cfg["channels"]
        angles = self.cfg["angles"]
        timing = self.cfg["timing"]

        for i, k in enumerate(channels.keys()):
            add_field(left, f"channels.{k}", channels[k], i)

        for i, k in enumerate(angles.keys()):
            add_field(mid, f"angles.{k}", angles[k], i)

        for i, k in enumerate(timing.keys()):
            add_field(right, f"timing.{k}", timing[k], i)

        mode_box = ttk.Labelframe(settings_content, text="Mode", padding=10)
        mode_box.pack(fill="x", pady=(12, 0))
        self.primary_ribbon_color = tk.StringVar(
            value=normalize_ribbon_color(self.cfg["mode"].get("primary_ribbon_color", "BLUE"))
        )
        ttk.Label(mode_box, text="Primary ribbon color:").pack(side="left")
        ttk.Combobox(
            mode_box,
            textvariable=self.primary_ribbon_color,
            values=["BLUE", "GREEN"],
            width=8,
            state="readonly",
        ).pack(side="left", padx=(8, 0))

        self.sim_label = ttk.Label(settings_content, text="")
        self.sim_label.pack(anchor="w", pady=(12, 0))
        self.sim_label.configure(
            text="Bonnet library available."
            if HAS_BONNET
            else "Bonnet library not available. UI runs in simulation mode."
        )

    def on_apply_settings(self) -> None:
        if self.is_running:
            messagebox.showinfo("Running", "Stop the job before applying settings.")
            return
        try:
            for k in list(self.cfg["channels"].keys()):
                v = int(self.vars[f"channels.{k}"].get())
                self.cfg["channels"][k] = v
            for k in list(self.cfg["angles"].keys()):
                v = float(self.vars[f"angles.{k}"].get())
                self.cfg["angles"][k] = v
            for k in list(self.cfg["timing"].keys()):
                v = float(self.vars[f"timing.{k}"].get())
                self.cfg["timing"][k] = v
            self.cfg["mode"]["primary_ribbon_color"] = normalize_ribbon_color(self.primary_ribbon_color.get())
        except Exception as e:
            messagebox.showerror("Invalid settings", f"Could not apply settings.\n\n{e}")
            return

        self.status_var.set("Settings applied.")
        self._rebuild_preview_and_counts()
        self._rebuild_fill_plan()
    
    def on_reload_config(self) -> None:
        """
        Reload config from typewriter_config.txt (or whatever path App was started with),
        then refresh UI elements that depend on cfg (monochrome vars, preview plan, etc.).
        """
        if self.is_running:
            messagebox.showinfo("Running", "Stop the job before reloading config.")
            return

        # Decide where the config path lives. See section 2 below.
        cfg_path = getattr(self, "config_path", "typewriter_config.txt")

        new_cfg, status = load_config(cfg_path)
        self.cfg = new_cfg

        # Sync mode UI to cfg
        self.mono_enabled.set(bool(self.cfg["mode"]["monochrome_enabled"]))
        self.mono_color.set(str(self.cfg["mode"]["monochrome_color"]))
        if hasattr(self, "primary_ribbon_color"):
            self.primary_ribbon_color.set(normalize_ribbon_color(self.cfg["mode"].get("primary_ribbon_color", "BLUE")))

        # Refresh Settings tab entry boxes (if already built)
        if hasattr(self, "vars") and isinstance(self.vars, dict):
            # Channels
            for k in self.cfg["channels"].keys():
                key = f"channels.{k}"
                if key in self.vars:
                    self.vars[key].set(str(self.cfg["channels"][k]))

            # Angles
            for k in self.cfg["angles"].keys():
                key = f"angles.{k}"
                if key in self.vars:
                    self.vars[key].set(str(self.cfg["angles"][k]))

            # Timing
            for k in self.cfg["timing"].keys():
                key = f"timing.{k}"
                if key in self.vars:
                    self.vars[key].set(str(self.cfg["timing"][k]))

        # Rebuild preview and timing estimates using the new cfg
        self._rebuild_preview_and_counts()
        self._rebuild_fill_plan()

        self.status_var.set(status)


    # -------- File actions --------
    def on_load_json(self) -> None:
        if self.is_running:
            messagebox.showinfo("Running", "Stop the job before loading a new file.")
            return

        path = filedialog.askopenfilename(
            title="Select a JSON instruction file",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            data, grid, steps = parse_json_to_models(path)
        except Exception as e:
            messagebox.showerror("Load failed", f"Could not load JSON.\n\n{e}")
            return

        self.loaded_path = path
        self.fill_file_var.set(path)
        self.loaded_json = data
        self.grid_model = grid
        self.steps = steps

        self.mono_enabled.set(bool(self.cfg["mode"]["monochrome_enabled"]))
        self.mono_color.set(str(self.cfg["mode"]["monochrome_color"]))
        if hasattr(self, "primary_ribbon_color"):
            self.primary_ribbon_color.set(normalize_ribbon_color(self.cfg["mode"].get("primary_ribbon_color", "BLUE")))

        self._refresh_steps_view()
        self._render_edit_tab()
        self._rebuild_preview_and_counts()
        self._rebuild_fill_plan()

        self.btn_start.configure(state="normal")
        self.btn_pause.configure(state="disabled")
        self.btn_stop.configure(state="disabled")
        self.btn_save.configure(state="normal")

        self.status_var.set(f"Loaded: {path}")

    def on_save_json(self) -> None:
        if not self.steps:
            return
        path = filedialog.asksaveasfilename(
            title="Save JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            out = export_models_to_json(self.loaded_json, self.grid_model, self.steps)
            import json as _json
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(out, f, indent=2)
            self.status_var.set(f"Saved: {path}")
        except Exception as e:
            messagebox.showerror("Save failed", f"Could not save JSON.\n\n{e}")

    # -------- Preview + counts --------
    def _rebuild_preview_and_counts(self) -> None:
        if not self.steps:
            self.current_plan = None
            self.metrics_var.set("")
            self.progress_text_var.set("Ready.")
            self._clear_preview_canvas()
            return

        try:
            plan = compile_runtime_plan(self.steps, self.cfg, self.loaded_json, self.grid_model)
        except Exception as e:
            self.current_plan = None
            self.metrics_var.set(f"Could not compile runtime plan.\n{e}")
            self._clear_preview_canvas()
            return

        self.current_plan = plan
        self.done_expanded_count = 0
        self._reset_performance_estimator()
        self.progress.configure(value=0, maximum=max(1, len(plan.expanded_cmds)))
        self._refresh_metrics_text()

        self._draw_preview_grid(plan.preview_grid)
        self._update_progress_text()

    def _clear_preview_canvas(self) -> None:
        self.preview_canvas.delete("all")
        self.preview_printed = []
        self.preview_rects = []
        self.active_cell = None
        self.preview_grid_model = None

    def _on_preview_canvas_configure(self, _event: tk.Event) -> None:
        if self.preview_grid_model:
            self._draw_preview_grid(self.preview_grid_model, preserve_printed=True)

    def _draw_preview_grid(self, grid: GridModel, preserve_printed: bool = False) -> None:
        previous_printed = self.preview_printed if preserve_printed else []
        previous_active = self.active_cell if preserve_printed else None

        self.preview_canvas.delete("all")
        self.preview_grid_model = grid

        rows, cols = grid.rows, grid.cols
        if (
            preserve_printed
            and len(previous_printed) == rows
            and all(len(row) == cols for row in previous_printed)
        ):
            self.preview_printed = [row[:] for row in previous_printed]
        else:
            self.preview_printed = [[False for _ in range(cols)] for _ in range(rows)]
        self.preview_rects = [[-1 for _ in range(cols)] for _ in range(rows)]
        self.active_cell = previous_active

        canvas_w = max(1, self.preview_canvas.winfo_width())
        canvas_h = max(1, self.preview_canvas.winfo_height())
        margin = 8
        available_w = max(1, canvas_w - (2 * margin))
        available_h = max(1, canvas_h - (2 * margin))

        cell_w = available_w / max(1, cols)
        cell_h = available_h / max(1, rows)
        cell = max(1.0, min(cell_w, cell_h))
        grid_w = cell * cols
        grid_h = cell * rows
        x_start = max(margin, (canvas_w - grid_w) / 2)
        y_start = max(margin, (canvas_h - grid_h) / 2)
        outline = "#7a7a7a" if cell >= 4 else ""
        outline_width = 1 if cell >= 4 else 0

        self.preview_canvas.configure(scrollregion=(0, 0, canvas_w, canvas_h))

        for r in range(rows):
            for c in range(cols):
                x0 = x_start + c * cell
                y0 = y_start + r * cell
                x1 = x_start + (c + 1) * cell
                y1 = y_start + (r + 1) * cell
                fill = self._preview_cell_color(grid.cells[r][c], printed=self.preview_printed[r][c])
                rect = self.preview_canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=outline_width)
                self.preview_rects[r][c] = rect

        if self.active_cell is not None:
            r, c = self.active_cell
            if 0 <= r < rows and 0 <= c < cols:
                self.preview_canvas.itemconfigure(self.preview_rects[r][c], outline="#111111", width=max(2, outline_width))

    def _preview_cell_color(self, v: int, printed: bool) -> str:
        if v == 1:
            return "#557aa8" if printed else "#1765c1"
        if v == 2:
            return "#5f8758" if printed else "#239a35"
        return "#b8b8b8" if printed else "#f7f7f7"

    def _set_preview_cell_printed(self, r: int, c: int, printed: bool) -> None:
        plan = self.current_plan
        if not plan:
            return
        if not (0 <= r < plan.preview_grid.rows and 0 <= c < plan.preview_grid.cols):
            return
        self.preview_printed[r][c] = printed
        rect = self.preview_rects[r][c]
        fill = self._preview_cell_color(plan.preview_grid.cells[r][c], printed=printed)
        self.preview_canvas.itemconfigure(rect, fill=fill)

    def _set_active_cell_outline(self, new_cell: Optional[Tuple[int, int]]) -> None:
        plan = self.current_plan
        if not plan:
            return

        if self.active_cell is not None:
            r0, c0 = self.active_cell
            if 0 <= r0 < plan.preview_grid.rows and 0 <= c0 < plan.preview_grid.cols:
                rect0 = self.preview_rects[r0][c0]
                self.preview_canvas.itemconfigure(rect0, outline="#7a7a7a", width=1)

        self.active_cell = new_cell

        if new_cell is None:
            return
        r1, c1 = new_cell
        if 0 <= r1 < plan.preview_grid.rows and 0 <= c1 < plan.preview_grid.cols:
            rect1 = self.preview_rects[r1][c1]
            self.preview_canvas.itemconfigure(rect1, outline="#111111", width=2)

    # -------- Execution --------
    def on_start(self) -> None:
        if not self.steps or self.is_running:
            return

        if not HAS_BONNET:
            messagebox.showwarning("Bonnet not available", "This system cannot access the Adafruit Servo Bonnet. Run on a Raspberry Pi with adafruit_servokit installed.")
            return

        try:
            plan = compile_runtime_plan(self.steps, self.cfg, self.loaded_json, self.grid_model)
        except Exception as e:
            messagebox.showerror("Plan error", f"Could not compile runtime plan.\n\n{e}")
            return

        self.execution_mode = "run"
        self.current_plan = plan
        self.done_expanded_count = 0
        self._reset_performance_estimator()
        self.progress.configure(value=0, maximum=max(1, len(plan.expanded_cmds)))
        self._draw_preview_grid(plan.preview_grid)
        self._refresh_steps_view(active_index=0)
        self._update_progress_text()

        self.stop_event.clear()
        self.run_event.set()

        self.is_running = True
        self.btn_start.configure(state="disabled")
        self.btn_pause.configure(state="normal", text="Pause")
        self.btn_stop.configure(state="normal")
        self.fill_btn_start.configure(state="disabled")

        self.worker_thread = threading.Thread(target=self._worker_run, args=(plan,), daemon=True)
        self.worker_thread.start()

    def on_pause(self) -> None:
        if not self.is_running or self.execution_mode != "run":
            return
        if self.run_event.is_set():
            self.run_event.clear()
            self.btn_pause.configure(text="Resume")
            self.status_var.set("Paused.")
        else:
            self.run_event.set()
            self.btn_pause.configure(text="Pause")
            self.status_var.set("Running.")

    def on_stop(self) -> None:
        if not self.is_running or self.execution_mode != "run":
            return
        self.stop_event.set()
        self.run_event.set()
        self.status_var.set("Stopping…")

    def _worker_run(self, plan: RuntimePlan) -> None:
        rig = ServoRig(self.cfg)
        direct_cmd = direct_print_cmd(self.cfg)
        correction_cmd = correction_print_cmd(self.cfg)
        idx = 0
        correction_engaged = False

        def record_perf(category: str, started_s: float) -> None:
            self.msg_queue.put(("perf_action", (category, time.monotonic() - started_s)))

        try:
            rig.setup()
            self.msg_queue.put(("status", "Running."))

            n = len(plan.expanded_cmds)
            while idx < n:
                if self.stop_event.is_set():
                    break

                self.run_event.wait()

                cmd = plan.expanded_cmds[idx]
                self.msg_queue.put(("active_expanded", idx))

                next_is_correction = (idx + 1 < n and plan.expanded_cmds[idx + 1] == correction_cmd)
                is_correction = (cmd == correction_cmd)
                end_of_correction_run = is_correction and (not next_is_correction)

                if cmd == direct_cmd:
                    started = time.monotonic()
                    rig.press_blue_key()
                    time.sleep(self.cfg["timing"]["BETWEEN_CHARS"])
                    record_perf("DIRECT_CHAR", started)

                elif cmd == CMD_SPACE:
                    started = time.monotonic()
                    rig.press_spacebar()
                    time.sleep(self.cfg["timing"]["BETWEEN_CHARS"])
                    record_perf("SPACE", started)

                elif cmd == CMD_NEW_LINE:
                    started = time.monotonic()
                    rig.press_return()
                    time.sleep(self.cfg["timing"]["BETWEEN_CHARS"])
                    record_perf("NEW_LINE", started)

                elif cmd == correction_cmd:
                    if not correction_engaged:
                        started = time.monotonic()
                        rig.engage_correction()
                        record_perf("CORRECTION_ENGAGE", started)
                        correction_engaged = True

                    started = time.monotonic()
                    rig.press_blue_key()
                    time.sleep(self.cfg["timing"]["BETWEEN_KEYS"])
                    rig.press_spacebar()
                    time.sleep(self.cfg["timing"]["BETWEEN_CHARS"])
                    record_perf("CORRECTION_CHAR", started)

                    if end_of_correction_run and correction_engaged:
                        started = time.monotonic()
                        rig.release_correction()
                        record_perf("CORRECTION_RELEASE", started)
                        correction_engaged = False

                else:
                    raise ValueError(f"Unsupported runtime command: {cmd}")

                idx += 1
                self.msg_queue.put(("progress_expanded_done", idx))

            try:
                rig.move_spacebar_to_rest()
            except Exception:
                pass

            self.msg_queue.put(("status", "Stopped." if self.stop_event.is_set() else "Done."))

        except Exception as e:
            self.msg_queue.put(("error", str(e)))
        finally:
            try:
                if correction_engaged:
                    try:
                        rig.release_correction()
                    except Exception:
                        pass
            finally:
                try:
                    rig.cleanup()
                except Exception:
                    pass
                self.msg_queue.put(("finished", None))

    # -------- UI updates --------
    def _refresh_steps_view(self, active_index: Optional[int] = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.steps):
            self.tree.insert("", "end", values=(i + 1, s.cmd, s.count, ""))

        plan = self.current_plan
        maxv = len(plan.expanded_cmds) if plan else len(self.tree.get_children())
        self.progress.configure(value=0, maximum=max(1, maxv))

    def _poll_queue(self) -> None:
        try:
            while True:
                msg, payload = self.msg_queue.get_nowait()

                if msg == "status":
                    self.status_var.set(str(payload))

                elif msg == "error":
                    messagebox.showerror("Runtime error", str(payload))
                    self.status_var.set("Error.")

                elif msg == "active_expanded":
                    expanded_idx = int(payload)
                    if self.execution_mode == "fill":
                        self._mark_fill_active_from_expanded(expanded_idx)
                        self._highlight_fill_active_cell(expanded_idx)
                    else:
                        self._mark_active_from_expanded(expanded_idx)
                        self._highlight_active_cell(expanded_idx)

                elif msg == "progress_expanded_done":
                    done = int(payload)
                    if self.execution_mode == "fill":
                        self.fill_done_expanded_count = done
                        self.fill_progress.configure(value=done)
                        self._mark_fill_done_from_expanded(done - 1)
                        self._grey_fill_printed_cell(done - 1)
                        self._update_fill_progress_text()
                    else:
                        self.done_expanded_count = done
                        self.progress.configure(value=done)
                        self._mark_done_from_expanded(done - 1)
                        self._grey_printed_cell(done - 1)
                        self._update_progress_text()

                elif msg == "perf_action" and self.execution_mode == "run":
                    category, elapsed_s = payload
                    self._record_performance_action(str(category), float(elapsed_s))

                elif msg == "finished":
                    completed_mode = self.execution_mode
                    self.is_running = False
                    self.btn_start.configure(state="normal" if self.steps else "disabled")
                    self.btn_pause.configure(state="disabled", text="Pause")
                    self.btn_stop.configure(state="disabled")
                    self.fill_btn_start.configure(
                        state="normal" if self.fill_count > 0 else "disabled"
                    )
                    self.fill_btn_pause.configure(state="disabled", text="Pause")
                    self.fill_btn_stop.configure(state="disabled")
                    if completed_mode == "fill":
                        self._set_fill_active_cell_outline(None)
                    else:
                        self._set_active_cell_outline(None)

        except queue.Empty:
            pass
        finally:
            self.after(60, self._poll_queue)

    def _mark_active_from_expanded(self, expanded_idx: int) -> None:
        plan = self.current_plan
        if not plan:
            return
        if not (0 <= expanded_idx < len(plan.expanded_to_condensed)):
            return
        step_idx = plan.expanded_to_condensed[expanded_idx]
        self._set_tree_active(step_idx)

    def _mark_done_from_expanded(self, expanded_idx: int) -> None:
        plan = self.current_plan
        if not plan:
            return
        if not (0 <= expanded_idx < len(plan.expanded_to_condensed)):
            return
        step_idx = plan.expanded_to_condensed[expanded_idx]
        self._set_tree_done(step_idx)

    def _set_tree_active(self, step_idx: int) -> None:
        items = self.tree.get_children()
        if not items or not (0 <= step_idx < len(items)):
            return

        for it in items:
            tags = set(self.tree.item(it, "tags"))
            if "active" in tags:
                tags.remove("active")
                self.tree.item(it, tags=tuple(tags))
                if self.tree.set(it, "state") == "Active":
                    self.tree.set(it, "state", "")

        item = items[step_idx]
        tags = set(self.tree.item(item, "tags"))
        tags.add("active")
        self.tree.set(item, "state", "Active")
        self.tree.item(item, tags=tuple(tags))
        self.tree.see(item)

    def _set_tree_done(self, step_idx: int) -> None:
        items = self.tree.get_children()
        if not items or not (0 <= step_idx < len(items)):
            return

        item = items[step_idx]
        tags = set(self.tree.item(item, "tags"))
        tags.add("done")
        self.tree.set(item, "state", "Done")
        self.tree.item(item, tags=tuple(tags))

    def _highlight_active_cell(self, expanded_idx: int) -> None:
        plan = self.current_plan
        if not plan:
            return
        if not (0 <= expanded_idx < len(plan.cell_by_expanded)):
            self._set_active_cell_outline(None)
            return
        rc = plan.cell_by_expanded[expanded_idx]
        self._set_active_cell_outline(rc)

    def _grey_printed_cell(self, expanded_idx: int) -> None:
        plan = self.current_plan
        if not plan:
            return
        if not (0 <= expanded_idx < len(plan.cell_by_expanded)):
            return
        rc = plan.cell_by_expanded[expanded_idx]
        if rc is None:
            return
        r, c = rc
        self._set_preview_cell_printed(r, c, True)

    def _mark_fill_active_from_expanded(self, expanded_idx: int) -> None:
        plan = self.fill_plan
        if not plan or not (0 <= expanded_idx < len(plan.expanded_to_condensed)):
            return
        self._set_fill_tree_active(plan.expanded_to_condensed[expanded_idx])

    def _mark_fill_done_from_expanded(self, expanded_idx: int) -> None:
        plan = self.fill_plan
        if not plan or not (0 <= expanded_idx < len(plan.expanded_to_condensed)):
            return
        step_idx = plan.expanded_to_condensed[expanded_idx]
        items = self.fill_tree.get_children()
        if not (0 <= step_idx < len(items)):
            return
        item = items[step_idx]
        tags = set(self.fill_tree.item(item, "tags"))
        tags.add("done")
        self.fill_tree.set(item, "state", "Done")
        self.fill_tree.item(item, tags=tuple(tags))

    def _set_fill_tree_active(self, step_idx: int) -> None:
        items = self.fill_tree.get_children()
        if not items or not (0 <= step_idx < len(items)):
            return
        for item in items:
            tags = set(self.fill_tree.item(item, "tags"))
            if "active" in tags:
                tags.remove("active")
                self.fill_tree.item(item, tags=tuple(tags))
                if self.fill_tree.set(item, "state") == "Active":
                    self.fill_tree.set(item, "state", "")
        item = items[step_idx]
        tags = set(self.fill_tree.item(item, "tags"))
        tags.add("active")
        self.fill_tree.set(item, "state", "Active")
        self.fill_tree.item(item, tags=tuple(tags))
        self.fill_tree.see(item)

    def _highlight_fill_active_cell(self, expanded_idx: int) -> None:
        plan = self.fill_plan
        if not plan or not (0 <= expanded_idx < len(plan.cell_by_expanded)):
            self._set_fill_active_cell_outline(None)
            return
        self._set_fill_active_cell_outline(plan.cell_by_expanded[expanded_idx])

    def _grey_fill_printed_cell(self, expanded_idx: int) -> None:
        plan = self.fill_plan
        if not plan or not (0 <= expanded_idx < len(plan.cell_by_expanded)):
            return
        rc = plan.cell_by_expanded[expanded_idx]
        if rc is None:
            return
        r, c = rc
        if plan.preview_grid.cells[r][c] != 3:
            return
        self.fill_preview_printed[r][c] = True
        self.fill_preview_canvas.itemconfigure(self.fill_preview_rects[r][c], fill="#777777")

    def _reset_performance_estimator(self) -> None:
        self.performance_totals = {}
        self.performance_counts = {}
        self.performance_line_samples = 0

    def _record_performance_action(self, category: str, elapsed_s: float) -> None:
        if elapsed_s < 0:
            return
        self.performance_totals[category] = self.performance_totals.get(category, 0.0) + elapsed_s
        self.performance_counts[category] = self.performance_counts.get(category, 0) + 1
        if category == "NEW_LINE":
            self.performance_line_samples += 1
        self._refresh_metrics_text()
        self._update_progress_text()

    def _performance_category_defaults(self) -> Dict[str, float]:
        t = self.cfg["timing"]
        press_time = float(t["PRESS_TIME"])
        between_keys = float(t["BETWEEN_KEYS"])
        between_chars = float(t["BETWEEN_CHARS"])
        new_line_delay = float(t["NEW_LINE_DELAY"])
        return_press_hold = float(t.get("RETURN_PRESS_HOLD", 1.0))
        post_blue_jitter = float(t.get("POST_BLUE_JITTER_DELAY", 0.06))
        spacebar_press_s = press_time + 0.25 + press_time
        blue_key_press_s = (2 * press_time) + post_blue_jitter

        return {
            "DIRECT_CHAR": blue_key_press_s + between_chars,
            "CORRECTION_CHAR": blue_key_press_s + between_keys + spacebar_press_s + between_chars,
            "SPACE": spacebar_press_s + between_chars,
            "NEW_LINE": return_press_hold + press_time + new_line_delay + between_chars,
            "CORRECTION_ENGAGE": float(t["CORR_ENGAGE_DELAY"]),
            "CORRECTION_RELEASE": float(t.get("CORR_RELEASE_MOVE_DELAY", 0.3)) + float(t.get("CORR_RELEASE_PAUSE", 0.3)),
        }

    def _performance_average(self, category: str, defaults: Dict[str, float]) -> float:
        count = self.performance_counts.get(category, 0)
        if count > 0:
            return self.performance_totals.get(category, 0.0) / count
        return defaults[category]

    def _estimate_performance_total_s(self, plan: RuntimePlan) -> Optional[float]:
        if self.performance_line_samples < 1:
            return None

        defaults = self._performance_category_defaults()
        correction_chars = int(plan.actions.get("CORRECTION_CHARS", 0))
        direct_chars = max(0, int(plan.actions["BLUE_CHARS"]) + int(plan.actions["GREEN_CHARS"]) - correction_chars)
        counts = {
            "DIRECT_CHAR": direct_chars,
            "CORRECTION_CHAR": correction_chars,
            "SPACE": int(plan.actions['SPACES']),
            "NEW_LINE": int(plan.actions['NEW_LINES']),
            "CORRECTION_ENGAGE": int(plan.keystrokes["CORRECTION_ENGAGES"]),
            "CORRECTION_RELEASE": int(plan.keystrokes["CORRECTION_RELEASES"]),
        }

        action_total = 0.0
        for category, count in counts.items():
            action_total += count * self._performance_average(category, defaults)

        setup_overhead_s = 3 * float(self.cfg["timing"].get("SERVO_REST_MOVE_DELAY", 0.2)) + 0.2
        return setup_overhead_s + action_total

    def _refresh_metrics_text(self) -> None:
        plan = self.current_plan
        if not plan:
            self.metrics_var.set("")
            return

        direct_color = "GREEN" if direct_print_cmd(self.cfg) == CMD_PRINT_GREEN else "BLUE"
        correction_color = "GREEN" if correction_print_cmd(self.cfg) == CMD_PRINT_GREEN else "BLUE"
        performance_total_s = self._estimate_performance_total_s(plan)
        if performance_total_s is None:
            performance_text = "pending until first line completes"
        else:
            performance_text = format_hms(performance_total_s)

        lines = []
        lines.append(f"Ribbon:  primary={direct_color}  correction={correction_color}")
        lines.append(
            f"Actions:  BLUE={plan.actions['BLUE_CHARS']}  GREEN={plan.actions['GREEN_CHARS']}  "
            f"SPACE={plan.actions['SPACES']}  NEW_LINE={plan.actions['NEW_LINES']}"
        )
        lines.append(
            f"Keystrokes:  BLUE key={plan.keystrokes['BLUE_KEY_PRESSES']}  SPACEBAR={plan.keystrokes['SPACEBAR_PRESSES']}  "
            f"RETURN={plan.keystrokes['RETURN_KEY_PRESSES']}  CORRECTION engages={plan.keystrokes['CORRECTION_ENGAGES']}"
        )
        lines.append(f"Estimated total time (timing-based): {format_hms(plan.total_est_s)}")
        lines.append(f"Estimated total time (performance-based): {performance_text}")
        self.metrics_var.set("\n".join(lines))

    def _update_progress_text(self) -> None:
        plan = self.current_plan
        if not plan:
            self.progress_text_var.set("Ready.")
            return

        total_cmds = max(1, len(plan.expanded_cmds))
        done = max(0, min(self.done_expanded_count, total_cmds))
        pct = (done / total_cmds) * 100.0

        done_est = plan.prefix_s[done] if 0 <= done < len(plan.prefix_s) else plan.prefix_s[-1]
        remaining_est = max(0.0, plan.total_est_s - done_est)

        self.progress_text_var.set(
            f"{pct:5.1f}% complete.  Estimated remaining: {format_hms(remaining_est)}  "
            f"Estimated total: {format_hms(plan.total_est_s)}"
        )

    def _update_time_estimates_tick(self) -> None:
        if self.is_running:
            if self.execution_mode == "fill":
                self._update_fill_progress_text()
            else:
                self._update_progress_text()
        self.after(200, self._update_time_estimates_tick)

    def on_reset_spacebar(self) -> None:
        if self.is_running:
            messagebox.showinfo("Running", "Stop the job before resetting the spacebar.")
            return
        if not HAS_BONNET:
            messagebox.showwarning("Bonnet not available", "This system cannot access the Adafruit Servo Bonnet.")
            return

        try:
            rig = ServoRig(self.cfg)
            rig.setup()
            try:
                rig.move_spacebar_to_rest()
            finally:
                rig.cleanup()
            self.status_var.set("Spacebar reset to resting position.")
        except Exception as e:
            messagebox.showerror("Reset failed", f"Could not reset spacebar.\n\n{e}")

    def _on_close(self) -> None:
        if self.is_running:
            self.stop_event.set()
            self.run_event.set()
        self.destroy()


def run_app(config_path: str = "typewriter_config.txt") -> None:
    app = App(config_path=config_path)
    app.mainloop()
