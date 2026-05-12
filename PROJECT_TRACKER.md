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
- Added `.fzpz` package generation from produced Fritzing artifacts.
- Fixed KiCad plugin loader syntax and improved PCM install documentation.
- Added front silkscreen extraction and rendering into generated SVG views.
- Fixed board projection orientation so generated geometry is not vertically mirrored.
- Cropped generated SVG canvas to board bounds to remove excessive gray padding in Fritzing.
- Filtered non-user-facing silkscreen content (component reference/value and component footprint silk).
- Added regression tests for silkscreen parsing/rendering, coordinate projection, filtering, and SVG sizing.
- Added `requirements.txt` dependency list.
- Added opt-in external repository integration testing (clone at test time, parse/generate/validate, no project copy in this repo).

## Current Focus

- Return to the original conversion path with stable baseline behavior:
	- keep current silkscreen support as-is (no vector path text conversion),
	- investigate compatibility issues seen on another board,
	- improve parser robustness without adding optional rendering complexity.

## Next Steps

- Capture and reproduce the new board issues with minimal fixtures.
- Add targeted failing tests for those board-specific failures before fixing code.
- Harden parser handling for board/footprint variants (text, layers, and geometry edge cases).
- Validate connector IDs/names/mappings on at least two real board designs.
- Validate generated `.fzp` schema assumptions against actual Fritzing import behavior.
- Add round-trip checks using Fritzing import/export where practical.
- Add tests around plugin behavior and end-to-end integration flow.
- Add CI to run tests automatically on push/PR.

## Notes

- Current baseline is stable: full test suite is passing.
- Vector text-to-path rendering was intentionally deferred to reduce troubleshooting noise.
- Immediate priority is correctness and portability across multiple real KiCad boards.

## New Board Issue Checklist

- [ ] Record board identity and source files (board name, KiCad version, commit/tag, and paths used for reproduction).
- [ ] Capture expected behavior for this board (connectors, silkscreen visibility, board outline, and canvas size).
- [ ] Capture actual behavior from current converter output (`board_model.json`, `fritzing_connectors.json`, `.fzp`, and generated SVGs).
- [ ] Document visual or functional mismatches with concise notes and screenshots.
- [ ] Isolate minimal reproducible input fixture for each distinct issue.
- [ ] Add one failing test per issue before implementing fixes.
- [ ] Classify each failure by area (`parser`, `connector mapping`, `silkscreen`, `svg projection`, `fzp packaging`).
- [ ] Implement fixes incrementally with tests kept green between changes.
- [ ] Re-run full regression suite and confirm no regressions on `basic-led-power` and `Quick_5V`.
- [ ] Validate resulting part in Fritzing and confirm issue closure criteria are met.
- [ ] Update this tracker with final outcomes and any new guardrail tests.
