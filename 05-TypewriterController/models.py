from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

CMD_PRINT_BLUE = "PRINT_BLUE"
CMD_PRINT_GREEN = "PRINT_GREEN"
CMD_SPACE = "SPACE"
CMD_NEW_LINE = "NEW_LINE"

RIBBON_BLUE = "BLUE"
RIBBON_GREEN = "GREEN"

LEGACY_BLUE = "BLUE"
LEGACY_GREEN = "GREEN"
LEGACY_SPACE = "SPACE"
LEGACY_SPACEBAR = "SPACEBAR"


@dataclass
class Step:
    cmd: str
    count: int


@dataclass
class GridModel:
    rows: int
    cols: int
    cells: List[List[int]]  # 0=OFF, 1=BLUE, 2=GREEN, 3=BLACK preview

    def clamp(self, allow_preview_black: bool = False) -> None:
        valid_values = (0, 1, 2, 3) if allow_preview_black else (0, 1, 2)
        for r in range(self.rows):
            for c in range(self.cols):
                if self.cells[r][c] not in valid_values:
                    self.cells[r][c] = 0


def rle_compile_grid_to_steps(grid: GridModel) -> List[Step]:
    steps: List[Step] = []
    for r in range(grid.rows):
        run_cmd: Optional[str] = None
        run_len = 0

        def flush_run() -> None:
            nonlocal run_cmd, run_len
            if run_cmd and run_len > 0:
                steps.append(Step(run_cmd, run_len))
            run_cmd = None
            run_len = 0

        for c in range(grid.cols):
            v = grid.cells[r][c]
            if v == 0:
                cmd = CMD_SPACE
            elif v == 1:
                cmd = CMD_PRINT_BLUE
            else:
                cmd = CMD_PRINT_GREEN

            if run_cmd is None:
                run_cmd = cmd
                run_len = 1
            elif cmd == run_cmd:
                run_len += 1
            else:
                flush_run()
                run_cmd = cmd
                run_len = 1

        flush_run()
        steps.append(Step(CMD_NEW_LINE, 1))
    return steps


def normalize_ribbon_color(value: Any) -> str:
    color = str(value).upper()
    return RIBBON_GREEN if color == RIBBON_GREEN else RIBBON_BLUE


def primary_ribbon_color(cfg: Dict[str, Any]) -> str:
    mode = cfg.get("mode", {})
    if not isinstance(mode, dict):
        mode = {}
    return normalize_ribbon_color(mode.get("primary_ribbon_color", RIBBON_BLUE))


def direct_print_cmd(cfg: Dict[str, Any]) -> str:
    return CMD_PRINT_GREEN if primary_ribbon_color(cfg) == RIBBON_GREEN else CMD_PRINT_BLUE


def correction_print_cmd(cfg: Dict[str, Any]) -> str:
    return CMD_PRINT_BLUE if primary_ribbon_color(cfg) == RIBBON_GREEN else CMD_PRINT_GREEN


def compile_fill_steps(
    source_steps: List[Step],
    cfg: Dict[str, Any],
    loaded_json: Dict[str, Any],
    grid_model: Optional[GridModel],
) -> Tuple[List[Step], GridModel, int]:
    """Build a third-color pass that prints only cells originally encoded as SPACE."""
    rows: List[List[str]] = [[]]
    for step in source_steps:
        if step.cmd == CMD_NEW_LINE:
            for _ in range(step.count):
                rows.append([])
            continue
        rows[-1].extend([step.cmd] * step.count)

    if rows and not rows[-1] and source_steps and source_steps[-1].cmd == CMD_NEW_LINE:
        rows.pop()

    meta_rows = _safe_meta_int_any(loaded_json, "rows", "overall_rows")
    meta_cols = _safe_meta_int_any(loaded_json, "cols", "overall_cols")
    preview_rows = max(grid_model.rows if grid_model else 0, len(rows), meta_rows or 0, 1)
    preview_cols = max(
        grid_model.cols if grid_model else 0,
        max((len(row) for row in rows), default=0),
        meta_cols or 0,
        1,
    )
    preview_cells = [[0 for _ in range(preview_cols)] for _ in range(preview_rows)]

    direct_cmd = direct_print_cmd(cfg)
    fill_steps: List[Step] = []
    fill_count = 0

    def append_step(cmd: str, count: int = 1) -> None:
        if count < 1:
            return
        if fill_steps and fill_steps[-1].cmd == cmd:
            fill_steps[-1].count += count
        else:
            fill_steps.append(Step(cmd, count))

    for row_index in range(preview_rows):
        row = rows[row_index] if row_index < len(rows) else []
        last_fill_col = -1
        for col, cmd in enumerate(row):
            if cmd == CMD_SPACE:
                last_fill_col = col
                if col < preview_cols:
                    preview_cells[row_index][col] = 3
                fill_count += 1

        for cmd in row[: last_fill_col + 1]:
            append_step(direct_cmd if cmd == CMD_SPACE else CMD_SPACE)
        append_step(CMD_NEW_LINE)

    return fill_steps, GridModel(preview_rows, preview_cols, preview_cells), fill_count


def compile_fill_runtime_plan(
    source_steps: List[Step],
    cfg: Dict[str, Any],
    loaded_json: Dict[str, Any],
    grid_model: Optional[GridModel],
) -> Tuple[List[Step], "RuntimePlan", int]:
    fill_steps, preview_grid, fill_count = compile_fill_steps(
        source_steps, cfg, loaded_json, grid_model
    )
    fill_cfg = {
        **cfg,
        "mode": {
            **cfg.get("mode", {}),
            "monochrome_enabled": False,
        },
    }
    plan = compile_runtime_plan(fill_steps, fill_cfg, loaded_json, preview_grid)
    return fill_steps, plan, fill_count


def parse_json_to_models(path: str) -> Tuple[Dict[str, Any], Optional[GridModel], List[Step]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    grid_model: Optional[GridModel] = None
    steps: List[Step] = []

    if isinstance(data, dict) and isinstance(data.get("grid"), dict):
        g = data["grid"]
        rows = int(g.get("rows", 0))
        cols = int(g.get("cols", 0))
        cells = g.get("cells")

        if rows > 0 and cols > 0 and isinstance(cells, list) and len(cells) == rows:
            parsed_cells: List[List[int]] = []
            ok = True
            for r in range(rows):
                row = cells[r]
                if not isinstance(row, list) or len(row) != cols:
                    ok = False
                    break
                parsed_row: List[int] = []
                for c in range(cols):
                    try:
                        parsed_row.append(int(row[c]))
                    except Exception:
                        parsed_row.append(0)
                parsed_cells.append(parsed_row)

            if ok:
                grid_model = GridModel(rows=rows, cols=cols, cells=parsed_cells)
                grid_model.clamp()
                steps = rle_compile_grid_to_steps(grid_model)
                return data, grid_model, steps

    seq = data.get("typewriter_sequence")
    if not isinstance(seq, list):
        raise ValueError("JSON must include either a valid 'grid' or a 'typewriter_sequence' list.")

    for step in seq:
        if not isinstance(step, dict) or len(step) != 1:
            raise ValueError(f"Invalid step format: {step!r}")
        cmd, value = next(iter(step.items()))
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"Invalid repeat value for {cmd}: {value!r}")

        if cmd == LEGACY_BLUE:
            steps.append(Step(CMD_PRINT_BLUE, value))
        elif cmd == LEGACY_GREEN:
            steps.append(Step(CMD_PRINT_GREEN, value))
        elif cmd in (CMD_SPACE, LEGACY_SPACE, LEGACY_SPACEBAR):
            steps.append(Step(CMD_SPACE, value))
        elif cmd == "NEW_LINE":
            steps.append(Step(CMD_NEW_LINE, value))
        else:
            raise ValueError(f"Unknown command in JSON: {cmd!r}")

    return data, None, steps


def export_models_to_json(metadata: Dict[str, Any], grid_model: Optional[GridModel], steps: List[Step]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    out["metadata"] = metadata.get("metadata", {})

    if grid_model:
        out["grid"] = {"rows": grid_model.rows, "cols": grid_model.cols, "cells": grid_model.cells}

    seq: List[Dict[str, int]] = []
    for s in steps:
        if s.cmd == CMD_PRINT_BLUE:
            seq.append({LEGACY_BLUE: s.count})
        elif s.cmd == CMD_PRINT_GREEN:
            seq.append({LEGACY_GREEN: s.count})
        elif s.cmd == CMD_SPACE:
            seq.append({LEGACY_SPACE: s.count})
        elif s.cmd == CMD_NEW_LINE:
            seq.append({"NEW_LINE": s.count})
        else:
            raise ValueError(f"Cannot export unknown cmd: {s.cmd}")

    out["typewriter_sequence"] = seq
    return out


@dataclass
class RuntimePlan:
    expanded_cmds: List[str]
    expanded_to_condensed: List[int]
    cell_by_expanded: List[Optional[Tuple[int, int]]]
    preview_grid: GridModel
    durations_s: List[float]
    prefix_s: List[float]
    total_est_s: float
    keystrokes: Dict[str, int]
    actions: Dict[str, int]


def _safe_meta_int(loaded_json: Dict[str, Any], key: str) -> Optional[int]:
    md = loaded_json.get("metadata")
    if not isinstance(md, dict):
        return None
    v = md.get(key)
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _safe_meta_int_any(loaded_json: Dict[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        value = _safe_meta_int(loaded_json, key)
        if value is not None:
            return value
    return None


def _find_cmd_runs(expanded_cmds: List[str], target_cmd: str) -> List[Tuple[int, int]]:
    runs: List[Tuple[int, int]] = []
    i = 0
    n = len(expanded_cmds)
    while i < n:
        if expanded_cmds[i] != target_cmd:
            i += 1
            continue
        j = i
        while j + 1 < n and expanded_cmds[j + 1] == target_cmd:
            j += 1
        runs.append((i, j))
        i = j + 1
    return runs


def format_hms(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    s = int(round(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def compile_runtime_plan(
    condensed_steps: List[Step],
    cfg: Dict[str, Any],
    loaded_json: Dict[str, Any],
    grid_model: Optional[GridModel],
) -> RuntimePlan:
    expanded_cmds: List[str] = []
    expanded_to_condensed: List[int] = []

    mono_on = bool(cfg["mode"]["monochrome_enabled"])
    mono_color = str(cfg["mode"]["monochrome_color"])
    mono_cmd = CMD_PRINT_BLUE if mono_color == "BLUE" else CMD_PRINT_GREEN
    direct_cmd = direct_print_cmd(cfg)
    correction_cmd = correction_print_cmd(cfg)

    for i, s in enumerate(condensed_steps):
        cmd = s.cmd
        count = s.count

        if cmd == CMD_NEW_LINE:
            for _ in range(count):
                expanded_cmds.append(CMD_NEW_LINE)
                expanded_to_condensed.append(i)
        elif cmd == CMD_SPACE:
            for _ in range(count):
                expanded_cmds.append(CMD_SPACE)
                expanded_to_condensed.append(i)
        elif cmd in (CMD_PRINT_BLUE, CMD_PRINT_GREEN):
            if mono_on:
                for _ in range(count):
                    expanded_cmds.append(mono_cmd)
                    expanded_to_condensed.append(i)
            else:
                for _ in range(count):
                    expanded_cmds.append(cmd)
                    expanded_to_condensed.append(i)
        else:
            for _ in range(count):
                expanded_cmds.append(cmd)
                expanded_to_condensed.append(i)

    meta_rows = _safe_meta_int_any(loaded_json, "rows", "overall_rows")
    meta_cols = _safe_meta_int_any(loaded_json, "cols", "overall_cols")

    r = 0
    c = 0
    max_r = 0
    max_c = 0
    cell_by_expanded: List[Optional[Tuple[int, int]]] = [None] * len(expanded_cmds)

    for idx, cmd in enumerate(expanded_cmds):
        if cmd == CMD_NEW_LINE:
            r += 1
            c = 0
            max_r = max(max_r, r)
            continue
        cell_by_expanded[idx] = (r, c)
        max_r = max(max_r, r)
        max_c = max(max_c, c)
        c += 1

    if grid_model:
        preview_rows = grid_model.rows
        preview_cols = grid_model.cols
        preview_cells = [row[:] for row in grid_model.cells]
    else:
        preview_rows = max((meta_rows or 0), max_r + 1) if expanded_cmds else (meta_rows or 0) or 1
        preview_cols = max((meta_cols or 0), max_c + 1) if expanded_cmds else (meta_cols or 0) or 1
        preview_cells = [[0 for _ in range(preview_cols)] for _ in range(preview_rows)]
        for idx, cmd in enumerate(expanded_cmds):
            rc = cell_by_expanded[idx]
            if rc is None:
                continue
            rr, cc = rc
            if not (0 <= rr < preview_rows and 0 <= cc < preview_cols):
                continue
            if cmd == CMD_PRINT_BLUE:
                preview_cells[rr][cc] = 1
            elif cmd == CMD_PRINT_GREEN:
                preview_cells[rr][cc] = 2
            else:
                preview_cells[rr][cc] = 0

    preview_grid = GridModel(rows=preview_rows, cols=preview_cols, cells=preview_cells)
    preview_grid.clamp(allow_preview_black=True)

    t = cfg["timing"]
    press_time = float(t["PRESS_TIME"])
    between_keys = float(t["BETWEEN_KEYS"])
    between_chars = float(t["BETWEEN_CHARS"])
    new_line_delay = float(t["NEW_LINE_DELAY"])
    corr_engage_delay = float(t["CORR_ENGAGE_DELAY"])

    # Match ServoRig timing more closely
    return_press_hold = float(t.get("RETURN_PRESS_HOLD", 1.0))
    corr_release_overhead = float(t.get("CORR_RELEASE_MOVE_DELAY", 0.3)) + float(t.get("CORR_RELEASE_PAUSE", 0.3))
    post_blue_jitter = float(t.get("POST_BLUE_JITTER_DELAY", 0.06))
    spacebar_press_s = press_time + 0.25 + press_time
    blue_key_press_s = (2 * press_time) + post_blue_jitter

    durations_s: List[float] = []
    for cmd in expanded_cmds:
        if cmd == direct_cmd:
            durations_s.append(blue_key_press_s + between_chars)
        elif cmd == CMD_SPACE:
            durations_s.append(spacebar_press_s + between_chars)
        elif cmd == CMD_NEW_LINE:
            durations_s.append(return_press_hold + press_time + new_line_delay + between_chars)
        elif cmd == correction_cmd:
            durations_s.append(blue_key_press_s + between_keys + spacebar_press_s + between_chars)
        else:
            durations_s.append(between_chars)

    correction_runs = _find_cmd_runs(expanded_cmds, correction_cmd)
    for start, end in correction_runs:
        if 0 <= start < len(durations_s):
            durations_s[start] += corr_engage_delay
        if 0 <= end < len(durations_s):
            durations_s[end] += corr_release_overhead

    setup_overhead_s = 3 * float(t.get("SERVO_REST_MOVE_DELAY", 0.2)) + 0.2

    prefix_s: List[float] = [0.0]
    total = 0.0
    for d in durations_s:
        total += d
        prefix_s.append(total)
    total_est_s = setup_overhead_s + total

    actions = {
        "BLUE_CHARS": sum(1 for cmd in expanded_cmds if cmd == CMD_PRINT_BLUE),
        "GREEN_CHARS": sum(1 for cmd in expanded_cmds if cmd == CMD_PRINT_GREEN),
        "SPACES": sum(1 for cmd in expanded_cmds if cmd == CMD_SPACE),
        "NEW_LINES": sum(1 for cmd in expanded_cmds if cmd == CMD_NEW_LINE),
        "CORRECTION_CHARS": sum(1 for cmd in expanded_cmds if cmd == correction_cmd),
    }

    keystrokes = {
        "BLUE_KEY_PRESSES": actions["BLUE_CHARS"] + actions["GREEN_CHARS"],
        "SPACEBAR_PRESSES": actions["SPACES"] + actions["CORRECTION_CHARS"],
        "RETURN_KEY_PRESSES": actions["NEW_LINES"],
        "CORRECTION_ENGAGES": len(correction_runs),
        "CORRECTION_RELEASES": len(correction_runs),
    }

    return RuntimePlan(
        expanded_cmds=expanded_cmds,
        expanded_to_condensed=expanded_to_condensed,
        cell_by_expanded=cell_by_expanded,
        preview_grid=preview_grid,
        durations_s=durations_s,
        prefix_s=prefix_s,
        total_est_s=total_est_s,
        keystrokes=keystrokes,
        actions=actions,
    )
