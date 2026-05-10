# KiCad2Fritzing

Utility and future KiCad extension for generating Fritzing-compatible part assets from KiCad board layout data.

## Overview

This project is aimed at making documentation easier by converting KiCad board information into a Fritzing part that can be dropped into wiring diagrams.

Current status:
- Python package scaffold and CLI entry point are in place.
- Initial KiCad Action Plugin skeleton exists.
- Initial KiCad PCB parser extracts nets, footprints, and pads into an intermediate model.
- Starter connector mapping generates a Fritzing-oriented connector model.
- Minimal `.fzp` part generation is implemented from connector model data.
- SVG view generation uses parsed connector coordinates from the board model.
- Board outline is extracted from KiCad `Edge.Cuts` and used in generated SVG board shapes.
- Supported Edge.Cuts primitives now include `gr_rect`, `gr_line`, `gr_poly`, and `gr_arc`.
- Artifact consistency validation checks connector IDs across generated files.
- Reference artifacts are organized under `references/`.

## Repository Layout

- `src/kicad2fritzing/`: Main Python package.
- `src/kicad2fritzing/core/`: Extraction and conversion logic.
- `src/kicad2fritzing/kicad/`: KiCad extension/plugin integration code.
- `PROJECT_TRACKER.md`: Ongoing development checklist and focus tracking.
- `references/fritzing-parts/`: `.fzp` reference files.
- `references/kicad-exports/`: KiCad export SVG references.
- `references/kicad-projects/`: KiCad project references for parser/converter development.
- `references/samples/`: Misc sample and sketch artifacts.
- `scripts/build_kicad10_dist.py`: Build script for KiCad 10 distribution artifacts.
- `dist/`: Built KiCad extension artifacts (generated).
- `docs/KICAD10_EXTENSION_INSTALL.md`: KiCad 10 extension install instructions.

## Development Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e src
pip install -e "src[dev]"
```

## CLI Execution

```bash
kicad2fritzing path/to/board.kicad_pcb --out-dir build/fritzing-part
```

For now, this creates placeholder output to validate project wiring and flow.
It also writes an intermediate model file: `board_model.json`.
It now also writes a connector model file: `fritzing_connectors.json`.
It also writes a starter Fritzing part file: `generated_part.fzp`.
It also writes placeholder SVG view files: `icon.svg`, `breadboard.svg`, `schematic.svg`, `pcb.svg`.
It writes a validation report: `artifact_validation.json`.

## KiCad Extension Direction

The Action Plugin scaffold is available in `src/kicad2fritzing/kicad/plugin.py`.

Planned behavior:
- Run from KiCad PCB Editor.
- Read current open board.
- Emit starter Fritzing artifacts next to the board file.

## KiCad 10 Extension Packaging And Install

Build distribution artifacts:

```bash
python3 scripts/build_kicad10_dist.py
```

Generated artifacts:
- `dist/kicad10-action-plugin/KiCad2Fritzing/`
- `dist/KiCad2Fritzing-kicad10-action-plugin.zip`

Install path on macOS for KiCad 10 Action Plugins:
- `~/Library/Application Support/kicad/10.0/scripting/plugins`

Detailed install steps are in:
- `docs/KICAD10_EXTENSION_INSTALL.md`

## Next Steps

- Parse `.kicad_pcb` and map pads/nets into Fritzing connector model.
- Generate Fritzing package outputs (`.fzp` + SVG views).
- Add tests for board parsing and connector mapping.

## Testing

Run tests from repository root:

```bash
. .venv/bin/activate
pytest
```

Current tests cover:
- Placeholder extractor output creation.
- Intermediate board model and connector model generation.
- Minimal `.fzp` generation from connector model.
- Placeholder SVG generation and connector ID consistency validation.
- CLI argument parsing and output generation flow.
