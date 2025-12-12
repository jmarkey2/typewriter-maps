from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

CMD_PRINT_BLUE = "PRINT_BLUE"
CMD_PRINT_GREEN = "PRINT_GREEN"
CMD_SPACE = "SPACE"
CMD_NEW_LINE = "NEW_LINE"

LEGACY_BLUE = "BLUE"
LEGACY_GREEN = "GREEN"


@dataclass
class Step:
    cmd: str
    count: int


@dataclass
class GridModel:
    rows: int
    cols: int
    cells: List[List[int]]  # 0=OFF, 1=BLUE, 2=GREEN

    def clamp(self) -> None:
        for r in range(self.rows):
            for c in range(self.cols):
                if self.cells[r][c] not in (0, 1, 2):
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
        elif cmd == CMD_SPACE or cmd == "SPACE":
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
            seq.append({"SPACE": s.count})
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


def _find_green_runs(expanded_cmds: List[str]) -> List[Tuple[int, int]]:
    runs: List[Tuple[int, int]] = []
    i = 0
    n = len(expanded_cmds)
    while i < n:
        if expanded_cmds[i] != CMD_PRINT_GREEN:
            i += 1
            continue
        j = i
        while j + 1 < n and expanded_cmds[j + 1] == CMD_PRINT_GREEN:
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

    meta_rows = _safe_meta_int(loaded_json, "rows")
    meta_cols = _safe_meta_int(loaded_json, "cols")

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
    preview_grid.clamp()

    t = cfg["timing"]
    press_time = float(t["PRESS_TIME"])
    between_keys = float(t["BETWEEN_KEYS"])
    between_chars = float(t["BETWEEN_CHARS"])
    new_line_delay = float(t["NEW_LINE_DELAY"])
    corr_engage_delay = float(t["CORR_ENGAGE_DELAY"])

    # Match ServoRig timing more closely
    return_press_hold = float(t.get("RETURN_PRESS_HOLD", 1.0))
    corr_release_overhead = float(t.get("CORR_RELEASE_MOVE_DELAY", 0.3)) + float(t.get("CORR_RELEASE_PAUSE", 0.3))
    space_toggle_delay = float(t.get("SPACE_TOGGLE_DELAY", 1.0))
    post_blue_jitter = float(t.get("POST_BLUE_JITTER_DELAY", 0.06))

    durations_s: List[float] = []
    for cmd in expanded_cmds:
        if cmd == CMD_PRINT_BLUE:
            durations_s.append((2 * press_time) + post_blue_jitter + between_chars)
        elif cmd == CMD_SPACE:
            durations_s.append(space_toggle_delay + between_chars)
        elif cmd == CMD_NEW_LINE:
            durations_s.append(return_press_hold + press_time + new_line_delay + between_chars)
        elif cmd == CMD_PRINT_GREEN:
            durations_s.append((2 * press_time) + post_blue_jitter + between_keys + space_toggle_delay + between_chars)
        else:
            durations_s.append(between_chars)

    green_runs = _find_green_runs(expanded_cmds)
    for start, end in green_runs:
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
    }

    keystrokes = {
        "BLUE_KEY_PRESSES": actions["BLUE_CHARS"] + actions["GREEN_CHARS"],
        "SPACEBAR_PRESSES": actions["SPACES"] + actions["GREEN_CHARS"],
        "RETURN_KEY_PRESSES": actions["NEW_LINES"],
        "CORRECTION_ENGAGES": len(green_runs),
        "CORRECTION_RELEASES": len(green_runs),
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
