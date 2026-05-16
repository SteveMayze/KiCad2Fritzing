# KiCad 10 Extension Install Guide

This project ships as a KiCad **Plugin and Content Manager (PCM)** compatible addon package.

---

## 1. Build the PCM Package

From the repository root:

```
python3 scripts/build_kicad10_dist.py
```

For publication/release builds (strict icon checks enabled):

```
python3 scripts/build_kicad10_dist.py --release
```

If `--release` reports missing icon assets, see icon requirements in
`docs/KICAD_PLUGIN_ICON_SPEC.md` and add the required files before rebuilding.

Artifacts produced:

| Path | Purpose |
|------|---------|
| `dist/pcb2fritzing-pcm/` | Exploded archive (useful for inspection / debugging) |
| `dist/PCB2FritzingPart-pcm.zip` | PCM-installable zip — use this to install |

### Before distributing publicly

Open `scripts/build_kicad10_dist.py` and update the `METADATA` dictionary at the top of
the file with your real details:

- `identifier` — reverse-DNS identifier, e.g. `com.github.YOURUSERNAME.pcb2fritzing`
- `author.name` and `author.contact.web`
- `resources.homepage`

Then re-run the build script.

---

## 2. Install via Plugin and Content Manager ("Install from File…")

This is the recommended method and requires no manual folder copying.

1. Open **KiCad** (the main launcher, not the PCB editor).
2. Click **Plugin and Content Manager** (the puzzle-piece icon on the toolbar).
3. In the PCM dialog click **Install from File…** (bottom-left).
4. Browse to `dist/PCB2FritzingPart-pcm.zip` and open it.
5. KiCad will validate the package metadata and install it.
6. Click **Apply Pending Changes**.
7. Restart KiCad when prompted.

---

## 3. Use the Plugin

1. Open the **PCB Editor** (`pcbnew`) with an existing `.kicad_pcb` board.
2. Select **Tools → External Plugins → PCB to Fritzing Part**.
3. The plugin writes conversion output into a `fritzing-part/` folder alongside the open board file.

---

## Archive Structure (for reference)

The PCM zip follows the required KiCad addon layout:

```
PCB2FritzingPart-pcm.zip
├── metadata.json                    # PCM package descriptor
├── plugins/
│   ├── __init__.py                 # compatibility loader entry
│   ├── pcb2fritzing_action.py      # top-level action plugin entry (scanned by KiCad)
│   └── pcb2fritzing/               # helper package
│       ├── __init__.py
│       ├── cli.py
│       ├── core/
│       └── kicad/
└── resources/                       # optional 64×64 icon.png
```

The `metadata.json` in the zip does **not** contain `download_url`, `download_sha256`,
or `download_size` — those fields belong only in the repository submission metadata,
not in the archive itself (per the KiCad PCM specification).

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| PCM rejects the zip | Ensure you ran the build script — do not manually re-zip the exploded directory, as path order matters |
| Plugin does not appear in Tools → External Plugins | In PCM uninstall PCB to Fritzing Part, close KiCad, reinstall from `dist/PCB2FritzingPart-pcm.zip`, then restart KiCad |
| Import error on run | Check the KiCad scripting console (`pcbnew` → View → Scripting Console) for the Python traceback |
| `pcbnew` module not found | The plugin must be run from inside KiCad; it cannot be executed standalone |
| Reveal Plugins folder is empty | Expected for PCM installs in KiCad 10. PCM installs plugin packages under `~/Documents/KiCad/10.0/3rdparty/plugins/<package-id>/`, not in the legacy `scripting/plugins` folder |
| Still missing after reinstall | Print the plugin discovery log and share it: `cat ~/Documents/KiCad/10.0/3rdparty/plugins/com_github_SteveMayze_pcb2fritzing/pcb2fritzing_plugin_discovery.log` (or replace `SteveMayze` with your own package username if different) |
| SWIG bindings unavailable | KiCad 10 deprecated SWIG Python bindings; they may not be included or enabled in your KiCad build. Check the log file for `✗ pcbnew import failed` message |

