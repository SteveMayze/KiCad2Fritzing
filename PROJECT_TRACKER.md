# Project Tracker

## Completed

- Set up Python package scaffold for the utility.
- Added CLI entrypoint and core extractor placeholder.
- Added KiCad Action Plugin skeleton.
- Organized reference artifacts under `references/`.
- Added test framework with `pytest` and initial test coverage.
- Added a simple KiCad reference project (`basic-led-power`) with V+ and GND connector plus LED board.
- Implemented initial `.kicad_pcb` parser for nets, footprints, and pads.
- Implemented intermediate model output (`board_model.json`) with test coverage against the reference board.
- Implemented starter connector mapping from board model to Fritzing-oriented connector model.
- Implemented connector artifact output (`fritzing_connectors.json`) with test coverage.
- Implemented minimal generated Fritzing part output (`generated_part.fzp`) from connector model.
- Implemented placeholder SVG generation for icon/breadboard/schematic/pcb views.
- Implemented artifact consistency validation across `.fzp` and SVG connector references.
- Replaced placeholder SVG connector placement with board-coordinate-based geometry projection.
- Implemented Edge.Cuts board outline extraction and applied it in generated breadboard/pcb SVG geometry.
- Extended Edge.Cuts parsing to include `gr_line`, `gr_poly`, and `gr_arc` primitives.
- Added KiCad 10 distribution packaging script and generated dist artifacts.

## Current Focus

- Refine SVG generation using parsed board geometry and connector coordinates.

## Next Steps

- Parse `.kicad_pcb` and extract pads/nets needed for connector mapping.
- Generate starter Fritzing outputs (`.fzp` and SVG views).
- Add tests for board parsing, connector mapping, and failure paths.
- Add tests for malformed input handling and parser edge cases.
- Validate generated `.fzp` schema assumptions against real Fritzing import behavior.
- Add round-trip checks using actual Fritzing import/export where possible.
- Add tests around plugin behavior and integration flow.
- Add CI to run tests automatically.
