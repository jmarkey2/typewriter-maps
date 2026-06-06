# Typewriter Maps

![Long Island Typewriter Map Banner](assets/images/typewriter-map-banner-long-island.jpg)
_Long Island typewriter map._

Typewriter Maps is a workflow for turning geographic map grids into physical typewriter drawings using a modified Smith-Corona Deville C/T and a Raspberry Pi-controlled servo rig.

This project started during Day 9 ("analog") of the 30 Day Map Challenge. After making one map manually, it was clear the process worked but was too slow and error-prone for repetition. Manual builds often took 4-5+ hours and required constant focus to count color-specific keystrokes. This repository captures the automated pipeline that now makes repeatable map production practical.

## Quick Workflow (Broad Steps)

1. Create a grid in QGIS using the Python geoprocessing script in `01-Scratch-QGIS/00-PythonScripts-Geoprocessing/`.
2. Convert the grid/map output into machine-readable typing instructions using `03-Create-instructions-html/typewriter_helper_updated_split_export.html`.
3. Map grid property values to typewriter actions: `BLUE`, `GREEN`, or `SPACE`.
4. Move instruction files to the Raspberry Pi and execute them with the controller in `05-TypewriterController/`, using servos connected through an Adafruit Servo Bonnet.

## Repository Structure

- `01-Scratch-QGIS/`: QGIS projects, source geopackage data, and Python script(s) used to generate map-aligned grids.
- `02-Geojson_GRIDS-ONLY/`: exported GeoJSON grid/map outputs used as conversion inputs.
- `03-Create-instructions-html/`: browser-based helper that converts map/grid inputs into typewriter instruction sequences.
- `04-Instructions/`: generated instruction JSONs (and related split/export outputs) ready for execution.
- `05-TypewriterController/`: Raspberry Pi control scripts and config for servo-driven key actuation.
- `00-Misc-Notes_Inspo_FinalMaps/`: personal notes/inspiration/archive material, intentionally excluded from public tracking.

## Detailed Pipeline

### 1) Grid Creation in QGIS

- Work in the QGIS scratch environment under `01-Scratch-QGIS/`.
- Use `01-Scratch-QGIS/00-PythonScripts-Geoprocessing/make_grid_v2-2025-12-07.py` to generate map-specific grids.
- Save intermediate project/state in `.qgz` files and source data in the geopackage as needed.

### 2) GeoJSON Export

- Export or stage grid-only outputs in `02-Geojson_GRIDS-ONLY/` as `.geojson`.
- These files define the geometry and layout used for instruction generation.

### 3) Instruction Generation

- Open `03-Create-instructions-html/typewriter_helper_updated_split_export.html`.
- Load a grid GeoJSON and choose the property field that should drive the typing instructions.
- Map each field value to one of three actions:
  - `BLUE`: print a blue cell.
  - `GREEN`: print a green cell.
  - `SPACE`: press the space bar to leave blank paper.
- Save generated instruction sets into `04-Instructions/` as `.json` (or split/zipped collections for larger works).

### 4) Raspberry Pi Execution

- Transfer generated instruction files from `04-Instructions/` to the Pi environment, or pull the latest repository state on the Pi.
- Run the controller from `05-TypewriterController/` to execute the sequence on hardware.
- The controller supports `BLUE`, `GREEN`, `SPACE`, and `NEW_LINE` instruction steps.
- Use the config in `05-TypewriterController/typewriter_config.txt` to tune motion parameters and channel mapping.

### Controller GUI

Start the controller from its directory so it can find `typewriter_config.txt`:

```bash
cd 05-TypewriterController
python3 main.py
```

The GUI provides four tabs:

- **Run** loads and executes the normal two-color instruction sequence.
- **Fill** reuses the same loaded JSON file for a third-color pass. Return the carriage to the original top-left position, replace the ribbon, and start Fill. Every original `SPACE` cell is printed using the primary ribbon position and shown as black in the preview. Existing colored cells become positioning spaces.
- **Edit** modifies grid cells or legacy instruction steps before execution.
- **Settings** updates hardware channels, angles, timing, and primary ribbon color for the current session. The settings form scrolls on smaller Raspberry Pi displays, while its Apply and Reload buttons remain accessible.

Fill plans preserve every `NEW_LINE`, retain leading and interior spaces needed for alignment, and omit trailing spacebar presses when Return can move directly to the next row. Rows without fill cells use only Return. If an instruction file contains no `SPACE` actions, Fill remains disabled because there is nothing to print.

## Hardware Context

The current rig uses:

- Typewriter: Smith-Corona Deville C/T
- Controller: Raspberry Pi 4
- Driver board: Adafruit Servo Bonnet
- Power: bench DC power supply
- Stability mod: 680 uF capacitor soldered into bonnet through-holes (significantly reduced actuation mistakes)

Four metal-gear servos currently actuate:

- `3/#` key
- carriage return
- space bar
- color toggle lever

Ribbon usage is primarily blue/green, with occasional black/red. Reliability came from repeated calibration of servo angles and timing.

![Modified Typewriter with Servo Rig](assets/images/typewriter-servo-rig.jpg)
_Modified Smith-Corona Deville C/T with servo rig._

## Configuration

Controller behavior is configured in:

- `05-TypewriterController/typewriter_config.txt`

Use this file to manage key servo mappings, motion limits, timing values that control print reliability, and the primary ribbon color. Set `mode.primary_ribbon_color` to `BLUE` or `GREEN` to match the physical ribbon orientation; generated instructions still describe the final printed colors.

## License

This project is licensed under the **MIT License**.
