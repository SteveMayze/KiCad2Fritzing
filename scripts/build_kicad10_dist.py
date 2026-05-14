"""Build a KiCad PCM-compatible addon zip for KiCad2Fritzing.

The Plugin and Content Manager (PCM) requires a specific archive layout:

    Archive root
    ├── metadata.json          # package metadata (no download_* fields)
    ├── plugins/
    │   ├── KiCad2Fritzing_action.py   # top-level action plugin entry
    │   └── kicad2fritzing/            # helper package
    │       └── ...
    └── resources/             # optional 64x64 icon.png

Run from repository root:
    python3 scripts/build_kicad10_dist.py

Outputs:
    dist/kicad2fritzing-pcm/   – exploded archive directory (inspect / debug)
    dist/KiCad2Fritzing-pcm.zip – PCM-installable zip ("Install from File…")

Before distributing publicly, update the TODO fields in METADATA below with
your real GitHub username / contact details, then re-run this script.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Load personal build config (gitignored).
# Copy scripts/build_config.json.example → scripts/build_config.json and
# fill in your details. Falls back to placeholder values if the file is absent.
# ---------------------------------------------------------------------------
_CONFIG_FILE = Path(__file__).parent / "build_config.json"

if _CONFIG_FILE.exists():
    _config: dict = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
else:
    _config = {}

_USERNAME = _config.get("username", "YOURUSERNAME")
_AUTHOR_NAME = _config.get("author_name", "PCB to Fritzing Part Contributors")
_CONTACT_WEB = _config.get("contact_web", f"https://github.com/{_USERNAME}/KiCad2Fritzing")

# ---------------------------------------------------------------------------
# Package metadata
# NOTE: download_url / download_sha256 / download_size are intentionally
# omitted here – they belong only in the repository submission metadata, NOT
# inside the archive itself (see KiCad PCM docs).
# ---------------------------------------------------------------------------
METADATA: dict = {
    "$schema": "https://go.kicad.org/pcm/schemas/v2",
    "name": "PCB to Fritzing Part",
    "description": "Export KiCad PCB layouts to Fritzing part assets.",
    "description_full": (
        "PCB to Fritzing Part is an action plugin for the KiCad PCB editor that "
        "extracts board layout information — footprints, pads, nets, and board "
        "outline — and generates Fritzing-compatible part files (.fzp) and SVG "
        "views ready for use in Fritzing."
    ),
    "identifier": f"com.github.{_USERNAME}.pcb2fritzing",
    "type": "plugin",
    "author": {
        "name": _AUTHOR_NAME,
        "contact": {
            "web": _CONTACT_WEB,
        },
    },
    "license": "MIT",
    "resources": {
        "homepage": _CONTACT_WEB,
    },
    "versions": [
        {
            "version": "0.1.0",
            "status": "development",
            "kicad_version": "10.0",
            "runtime": "swig",
        }
    ],
}

# Top-level action-plugin entry point written into plugins/.
# KiCad scans files at this level to discover ActionPlugin subclasses.
_PLUGIN_ENTRY = "\n".join(
    [
        '"""KiCad Action Plugin entry point for PCB to Fritzing Part (PCM-installed)."""',
        "from __future__ import annotations",
        "",
        "import sys",
        "from pathlib import Path",
        "",
        "# Always write a diagnostic log to help debug plugin discovery issues.",
        "_plugins_dir = Path(__file__).parent",
        '_log_path = _plugins_dir / "pcb2fritzing_plugin_discovery.log"',
        "",
        "def _write_log(msg: str) -> None:",
        '    """Write diagnostic log, creating or appending as needed."""',
        "    try:",
        "        existing = _log_path.read_text(encoding='utf-8') if _log_path.exists() else ''",
        '        _log_path.write_text(existing + msg + "\\n", encoding="utf-8")',
        "    except Exception:",
        "        pass  # Silently fail if we can't write the log",
        "",
        "# Make the sibling pcb2fritzing package importable when KiCad loads this file.",
        "if str(_plugins_dir) not in sys.path:",
        "    sys.path.insert(0, str(_plugins_dir))",
        "",
        '_write_log("=== PCB to Fritzing Part plugin loader invoked ===")',
        "",
        "# Check if SWIG pcbnew is available (required for ActionPlugin in KiCad 10).",
        "_pcbnew_available = False",
        "try:",
        "    import pcbnew  # type: ignore",
        "",
        "    _pcbnew_available = True",
        '    _write_log("✓ pcbnew module imported successfully")',
        "except ImportError as e:",
        '    _write_log(f"✗ pcbnew import failed: {e}")',
        '    _write_log("  (SWIG bindings deprecated in KiCad 10+; migrate to IPC API)")',
        "",
        "if not _pcbnew_available:",
        '    _write_log("Plugin registration skipped: SWIG runtime not available")',
        "else:",
        "    try:",
        "        from pcb2fritzing.kicad.plugin import register_plugin  # noqa: E402",
        "",
        "        _registered = register_plugin()",
        "        if _registered:",
        '            _write_log("✓ register_plugin() succeeded")',
        "        else:",
        '            _write_log("✗ register_plugin() returned False")',
        "    except Exception as e:",
        "        import traceback",
        "",
        '        _write_log("✗ Plugin registration failed with exception:")',
        "        _write_log(traceback.format_exc())",
    ]
)


def _copy_package(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)

    def _ignore(_dir: str, names: list[str]) -> set[str]:
        skip = {"__pycache__"}
        skip.update(n for n in names if n.endswith((".pyc", ".code-workspace")))
        return skip

    shutil.copytree(src, dst, ignore=_ignore)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    src_pkg = repo_root / "src" / "pcb2fritzing"
    dist_root = repo_root / "dist"
    archive_root = dist_root / "pcb2fritzing-pcm"

    # ---- (re)build the exploded archive directory --------------------------
    if archive_root.exists():
        shutil.rmtree(archive_root)
    archive_root.mkdir(parents=True)

    # metadata.json at archive root (no download_* keys – those are repo-only)
    (archive_root / "metadata.json").write_text(
        json.dumps(METADATA, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )

    # plugins/ – top-level entry + package
    plugins_dir = archive_root / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "__init__.py").write_text(_PLUGIN_ENTRY, encoding="utf-8")
    # Use lowercase action filename for stable behavior across platforms.
    (plugins_dir / "pcb2fritzing_action.py").write_text(_PLUGIN_ENTRY, encoding="utf-8")
    _copy_package(src_pkg, plugins_dir / "pcb2fritzing")

    # resources/ – placeholder; drop a 64×64 icon.png here to show in PCM
    (archive_root / "resources").mkdir()

    # ---- zip ---------------------------------------------------------------
    zip_path = dist_root / "PCB2FritzingPart-pcm.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=archive_root)

    print(f"Exploded archive : {archive_root}")
    print(f"PCM zip          : {zip_path}")
    print()
    if "YOURUSERNAME" in METADATA["identifier"]:
        print("⚠  TODO: update 'identifier', 'author', and 'resources' in METADATA")
        print("   at the top of this script before distributing publicly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
