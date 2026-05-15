# Project Tracker

## Completed

### Foundations

- Set up Python package scaffold for the utility.
- Added CLI entrypoint and core extractor placeholder.
- Added KiCad Action Plugin skeleton.
- Added `requirements.txt` dependency list.
- Organized reference artifacts under `references/`.
- Added a simple KiCad reference project (`basic-led-power`) with V+ and GND connector plus LED board.

### Parser, Model, and Connector Pipeline

- Implemented initial `.kicad_pcb` parser for nets, footprints, and pads.
- Implemented intermediate model output (`board_model.json`) with test coverage against the reference board.
- Implemented starter connector mapping from board model to Fritzing-oriented connector model.
- Implemented connector artifact output (`fritzing_connectors.json`) with test coverage.

### Fritzing Artifact Generation and Packaging

- Implemented minimal generated Fritzing part output (`generated_part.fzp`) from connector model.
- Implemented placeholder SVG generation for icon/breadboard/schematic/pcb views.
- Implemented artifact consistency validation across `.fzp` and SVG connector references.
- Added `.fzpz` package generation from produced Fritzing artifacts.
- Added KiCad 10 distribution packaging script and generated dist artifacts.

### SVG Geometry and Silkscreen Rendering

- Replaced placeholder SVG connector placement with board-coordinate-based geometry projection.
- Implemented Edge.Cuts board outline extraction and applied it in generated breadboard/pcb SVG geometry.
- Extended Edge.Cuts parsing to include `gr_line`, `gr_poly`, and `gr_arc` primitives.
- Added front silkscreen extraction and rendering into generated SVG views.
- Fixed board projection orientation so generated geometry is not vertically mirrored.
- Cropped generated SVG canvas to board bounds to remove excessive gray padding in Fritzing.
- Filtered non-user-facing silkscreen content (component reference/value and component footprint silk).
- 3D render mode now uses KiCad's basic viewer output.

### Plugin UX and Metadata

- Fixed KiCad plugin loader syntax and improved PCM install documentation.
- Added plugin output-directory UX improvements (create-directory browse, project-relative defaults, OS separator suffix behavior).
- Added render-option controls for soldermask/silkscreen color and pad scaling in plugin dialog; these are now part of the completed 3D render workflow.
- Added Fritzing metadata safeguards: generated `.fzp` now includes non-empty `family` and `type` properties.
- Added plugin dialog controls for user-defined Part Family and Part Type with defaults.

### Test and Validation Coverage

- Added test framework with `pytest` and initial test coverage.
- Added regression tests for silkscreen parsing/rendering, coordinate projection, filtering, and SVG sizing.
- Added regression tests for `.fzp` family/type defaults and override behavior.
- Added opt-in external repository integration testing (clone at test time, parse/generate/validate, no project copy in this repo).

## Current Focus

- Keep stabilizing cross-tool compatibility (KiCad export -> Fritzing import) with guardrail tests and small iterative UX fixes.
- 3D render investigation is complete.

## Status Board (Reconciled 2026-05-14)

| Workstream | Status | Notes |
| --- | --- | --- |
| Core parser + connector + artifact generation baseline | Done | Covered by existing Completed history and passing baseline tests. |
| KiCad-native SVG plotting investigation and plugin overlay path | Done | Investigation completed and integration path implemented in plugin flow. |
| Plugin UX improvements (output dir handling, metadata, render controls, diagnostics) | Done | Primary UX improvements delivered; render follow-up work is complete. |
| Cross-tool compatibility hardening on real boards | In Progress | Active focus for iterative fixes and guardrail test expansion. |
| SWIG -> IPC migration planning and execution | Open | Planned; no full adapter/execution migration merged yet. |
| CI on push/PR | Open | No GitHub Actions workflow currently present. |

## In Progress

- Capture and reproduce new board issues with minimal fixtures.
- Add targeted failing tests for board-specific failures before fixing code.
- Harden parser handling for board/footprint variants (text, layers, geometry edge cases).
- Validate connector IDs/names/mappings on at least two real board designs.
- Validate generated `.fzp` schema assumptions against actual Fritzing import behavior.

## Open

- Add optional CLI flags for `--part-family` and `--part-type` parity with plugin dialog metadata controls.
- Add round-trip checks using Fritzing import/export where practical.
- Add tests around plugin behavior and end-to-end integration flow.
- Plan migration away from deprecated KiCad SWIG Python bindings to the supported IPC plugin framework:
	- inventory current SWIG-dependent code paths in plugin/export flow,
	- design an IPC-backed adapter layer to preserve current CLI/plugin behavior,
	- implement and validate IPC-based plugin execution on KiCad 10+ while keeping test coverage green.
- Finalize the KiCad plugin dialog UX once the updated Balsamiq draft is ready:
	- review remaining field layout/labels and default value presentation deltas,
	- apply any remaining validation/input-hint polish from the revised design,
	- confirm final dialog behavior in KiCad with the revised design.
- Add CI to run tests automatically on push/PR.

## Blocked

- None currently tracked.

## Notes

- Current baseline is stable: full test suite is passing.
- Immediate priority is correctness and portability across multiple real KiCad boards.

### KiCad SVG Plotting Investigation (Started 2026-05-13)

Status: Completed (investigation + plugin integration path implemented).

- Confirmed KiCad Python API exposes programmatic plotting via `pcbnew.PLOT_CONTROLLER` and `pcbnew.PCB_PLOT_PARAMS`.
- Confirmed relevant plotting flow is available from Python:
	- create controller with board,
	- configure options via `GetPlotOptions()`,
	- choose layer via `SetLayer(...)`,
	- open output via `OpenPlotfile(...)`,
	- render via `PlotLayer()`,
	- finalize with `ClosePlot()`.
- Confirmed SVG-specific controls exist in API:
	- `SetFormat(pcbnew.PLOT_FORMAT_SVG)`,
	- `SetSvgPrecision(...)`,
	- `SetTextMode(...)` (important for text fidelity vs stroke behavior),
	- `SetPlotFrameRef(False)` and other layer/output options.
- Confirmed layer constants needed for this use case are available (`pcbnew.F_SilkS`, `pcbnew.Edge_Cuts`, etc.).
- Initial assessment: this is a viable path to generate a higher-fidelity silkscreen/outline overlay directly from KiCad plotting and should reduce custom text scaling complexity.

### KiCad SVG Plotting Spike Plan

Status: Completed.

- Plugin path can generate part output using KiCad-plotted `F_SilkS` and `Edge_Cuts` overlays.
- Native overlay integration is implemented for non-3D flow and can be used for 2D-render-first output.
- Current default behavior prefers 3D render mode.
- In 3D mode, advanced 2D overlay controls are disabled by default to avoid mixed-mode/ghosted output.
- Parser-based SVG generation remains available as the non-KiCad-runtime fallback path (CLI/tests).

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
