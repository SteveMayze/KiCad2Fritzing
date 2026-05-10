# KiCad 10 Extension Install Guide

This project provides a KiCad Action Plugin distribution for KiCad 10.

## Build The Distribution Artifacts

From repository root:

python3 scripts/build_kicad10_dist.py

Artifacts produced:
- `dist/kicad10-action-plugin/KiCad2Fritzing/` (folder install)
- `dist/KiCad2Fritzing-kicad10-action-plugin.zip` (zip install)

## Install On macOS (KiCad 10)

KiCad 10 Action Plugins are loaded from:

`~/Library/Application Support/kicad/10.0/scripting/plugins`

Install options:
1. Folder install:
   - Copy `dist/kicad10-action-plugin/KiCad2Fritzing` into the plugins directory.
2. Zip install:
   - Unzip `dist/KiCad2Fritzing-kicad10-action-plugin.zip`.
   - Copy the extracted `KiCad2Fritzing` folder into the plugins directory.

## Enable The Plugin

1. Launch KiCad PCB Editor (`pcbnew`).
2. Open an existing `.kicad_pcb` board.
3. Use Tools -> External Plugins and locate `KiCad2Fritzing`.
4. Run it.

The plugin writes conversion artifacts into a `fritzing-part` folder next to the open board.

## Troubleshooting

- If plugin does not appear, verify the folder path exactly matches the KiCad 10 plugin location.
- Restart KiCad after copying plugin files.
- Remove older copies of the plugin from other plugin folders to avoid conflicts.
