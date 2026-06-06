from __future__ import annotations

import sys
import unittest
from pathlib import Path

CONTROLLER_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = CONTROLLER_DIR.parent
sys.path.insert(0, str(CONTROLLER_DIR))

from config_loader import load_config
from models import (
    CMD_NEW_LINE,
    CMD_PRINT_BLUE,
    CMD_PRINT_GREEN,
    CMD_SPACE,
    Step,
    compile_fill_runtime_plan,
    compile_fill_steps,
    parse_json_to_models,
)


class FillPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg, _ = load_config(str(CONTROLLER_DIR / "typewriter_config.txt"))

    def test_inverts_space_cells_and_trims_trailing_movement(self) -> None:
        source = [
            Step(CMD_PRINT_BLUE, 2),
            Step(CMD_SPACE, 2),
            Step(CMD_PRINT_GREEN, 3),
            Step(CMD_NEW_LINE, 1),
        ]

        fill_steps, preview, fill_count = compile_fill_steps(source, self.cfg, {}, None)

        self.assertEqual(fill_count, 2)
        self.assertEqual(
            [(step.cmd, step.count) for step in fill_steps],
            [(CMD_SPACE, 2), (CMD_PRINT_BLUE, 2), (CMD_NEW_LINE, 1)],
        )
        self.assertEqual(preview.cells[0][:7], [0, 0, 3, 3, 0, 0, 0])

    def test_rows_without_fill_use_only_return(self) -> None:
        source = [
            Step(CMD_PRINT_BLUE, 4),
            Step(CMD_NEW_LINE, 1),
            Step(CMD_SPACE, 1),
            Step(CMD_NEW_LINE, 1),
        ]

        fill_steps, _, fill_count = compile_fill_steps(source, self.cfg, {}, None)

        self.assertEqual(fill_count, 1)
        self.assertEqual(
            [(step.cmd, step.count) for step in fill_steps],
            [(CMD_NEW_LINE, 1), (CMD_PRINT_BLUE, 1), (CMD_NEW_LINE, 1)],
        )

    def test_no_whitespace_produces_no_fill_characters(self) -> None:
        source = [Step(CMD_PRINT_BLUE, 3), Step(CMD_NEW_LINE, 2)]

        fill_steps, plan, fill_count = compile_fill_runtime_plan(
            source, self.cfg, {"metadata": {"overall_rows": 2, "overall_cols": 3}}, None
        )

        self.assertEqual(fill_count, 0)
        self.assertEqual([(step.cmd, step.count) for step in fill_steps], [(CMD_NEW_LINE, 2)])
        self.assertEqual(plan.actions["BLUE_CHARS"] + plan.actions["GREEN_CHARS"], 0)

    def test_primary_green_uses_direct_green_command(self) -> None:
        cfg = {**self.cfg, "mode": {**self.cfg["mode"], "primary_ribbon_color": "GREEN"}}

        fill_steps, _, _ = compile_fill_steps(
            [Step(CMD_SPACE, 1), Step(CMD_NEW_LINE, 1)], cfg, {}, None
        )

        self.assertEqual(fill_steps[0].cmd, CMD_PRINT_GREEN)

    def test_obama_center_example(self) -> None:
        path = PROJECT_DIR / "04-Instructions" / "ObamaCenter-2.5x3.5-typewriter_instructions.json"
        data, grid, source = parse_json_to_models(str(path))

        _, plan, fill_count = compile_fill_runtime_plan(source, self.cfg, data, grid)

        self.assertEqual(fill_count, 50)
        self.assertEqual(plan.actions["NEW_LINES"], 21)
        self.assertEqual(plan.actions["SPACES"], 31)
        self.assertEqual(plan.preview_grid.rows, 21)
        self.assertEqual(plan.preview_grid.cols, 25)


if __name__ == "__main__":
    unittest.main()
