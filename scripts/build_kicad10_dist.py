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
    dist/metadata.registry.json – KiCad registry metadata (download/install fields)

Before distributing publicly, copy scripts/build_config.json.example to
scripts/build_config.json and update its release/contact fields, then re-run
this script.

VERSION FORMAT
KiCad PCM requires versions to match: ^\d{1,4}(\.\d{1,4}(\.\d{1,6})?)?$
Valid examples:
  - "0" (single component)
  - "1.2" (major.minor)
  - "0.1.0" (major.minor.patch)
  - "1000.9999.999999" (maximum bounds)

Invalid examples (rejected):
  - "0.1.0-beta" (pre-release suffix not allowed)
  - "1.0.0-rc1" (release candidate not allowed)
  - "v1.0.0" (leading 'v' not allowed)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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
_VERSION = _config.get("version", "0.1.0")
_RELEASE_STATUS = _config.get("release_status", "development")
_KICAD_VERSION = _config.get("kicad_version", "10.0")
_RUNTIME = _config.get("runtime", "swig")
_RELEASE_TAG = str(_config.get("release_tag", "")).strip()
_RELEASE_ASSET_NAME = _config.get("release_asset_name", "PCB2FritzingPart-pcm.zip")
_DOWNLOAD_URL = str(_config.get("download_url", "")).strip()
_DOWNLOAD_SHA256 = str(_config.get("download_sha256", "YOUR_SHA256_HERE")).strip()
_DOWNLOAD_SIZE = int(_config.get("download_size", 0) or 0)
_INSTALL_SIZE = int(_config.get("install_size", 0) or 0)
_TAGS = _config.get(
    "tags",
    ["bom", "pcbnew", "html", "assembly", "documentation"],
)
_PLATFORMS = _config.get("platforms", ["linux", "macos", "windows"])

# ---------------------------------------------------------------------------
# Package metadata
# Release/distribution fields are sourced from build_config.json so you can
# bump versions and release links without editing this script.
# ---------------------------------------------------------------------------


def _resolve_download_url() -> str:
    if _DOWNLOAD_URL:
        return _DOWNLOAD_URL
    if _RELEASE_TAG and _USERNAME != "YOURUSERNAME":
        return (
            f"https://github.com/{_USERNAME}/KiCad2Fritzing/releases/download/"
            f"{_RELEASE_TAG}/{_RELEASE_ASSET_NAME}"
        )
    return "<DOWNLOAD_URL>"


def _build_metadata_base() -> dict:
    return {
        "$schema": "https://go.kicad.org/pcm/schemas/v2",
        "name": "PCB to Fritzing Part",
        "description": "Export KiCad PCB layouts to Fritzing part assets.",
        "description_full": (
            "PCB to Fritzing Part is an action plugin for the KiCad PCB editor that "
            "extracts board layout information — footprints, pads, outline — and "
            "generates Fritzing-compatible part files (.fzp) and SVG views ready for "
            "use in Fritzing."
        ),
        "identifier": f"com.github.{_USERNAME}.pcb2fritzing",
        "type": "plugin",
        "tags": [str(tag) for tag in _TAGS],
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
    }


def _version_base() -> dict:
    return {
        "version": _VERSION,
        "status": _RELEASE_STATUS,
        "kicad_version": _KICAD_VERSION,
        "platforms": [str(platform) for platform in _PLATFORMS],
        "runtime": _RUNTIME,
    }


def _build_archive_metadata() -> dict:
    metadata = _build_metadata_base()
    # Keep archive metadata stable by excluding volatile download/install fields.
    metadata["versions"] = [_version_base()]
    return metadata


def _build_registry_metadata(zip_path: Path, install_size: int) -> dict:
    metadata = _build_metadata_base()
    metadata["versions"] = [
        {
            **_version_base(),
            "download_sha256": _resolve_download_sha256(zip_path),
            "download_size": _resolve_download_size(zip_path),
            "download_url": _resolve_download_url(),
            "install_size": _INSTALL_SIZE if _INSTALL_SIZE > 0 else install_size,
        }
    ]
    return metadata


def _dir_size_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _resolve_download_sha256(zip_path: Path) -> str:
    if _DOWNLOAD_SHA256 and _DOWNLOAD_SHA256 != "YOUR_SHA256_HERE":
        return _DOWNLOAD_SHA256
    return _file_sha256(zip_path)


def _resolve_download_size(zip_path: Path) -> int:
    if _DOWNLOAD_SIZE > 0:
        return _DOWNLOAD_SIZE
    return int(zip_path.stat().st_size)

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
        "try:",
        "    import kipy  # type: ignore",
        '    _write_log("✓ kipy IPC client available")',
        "except ImportError as e:",
        '    _write_log(f"ℹ kipy IPC client not available: {e}")',
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a KiCad PCM-compatible addon zip for KiCad2Fritzing.",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="Fail if required icon assets are missing.",
    )
    return parser.parse_args()


def _validate_release_assets(
    toolbar_light_icon: Path,
    toolbar_dark_icon: Path,
    pcm_icon: Path,
) -> None:
    missing: list[Path] = []
    for icon_path in (toolbar_light_icon, toolbar_dark_icon, pcm_icon):
        if not icon_path.exists():
            missing.append(icon_path)

    if not missing:
        return

    print("\n✗ Release build blocked: required icon assets are missing:")
    for icon_path in missing:
        print(f"  - {icon_path}")
    raise SystemExit(2)


def _validate_version(version: str) -> None:
    """Validate version matches KiCad PCM schema regex.
    
    KiCad PCM requires: ^\d{1,4}(\.\d{1,4}(\.\d{1,6})?)?$
    
    Valid: "0", "1.2", "0.1.0", "1000.9999.999999"
    Invalid: "0.1.0-beta", "1.0.0-rc1", "v1.0.0"
    """
    pattern = r"^\d{1,4}(\.\d{1,4}(\.\d{1,6})?)?$"
    if not re.match(pattern, version):
        print(f"\n✗ Invalid version format: '{version}'")
        print(f"  Must match KiCad PCM schema: {pattern}")
        print()
        print("  Valid examples:")
        print('    "0"')
        print('    "1.2"')
        print('    "0.1.0"')
        print('    "1000.9999.999999"')
        print()
        print("  Invalid examples (rejected):")
        print('    "0.1.0-beta" (pre-release suffix)')
        print('    "1.0.0-rc1" (release candidate)')
        print('    "v1.0.0" (leading v)')
        raise SystemExit(1)


def main() -> int:
    args = _parse_args()
    
    # Validate version format first, before any build operations
    _validate_version(_VERSION)
    
    repo_root = Path(__file__).resolve().parents[1]
    src_pkg = repo_root / "src" / "pcb2fritzing"
    assets_dir = src_pkg / "kicad" / "assets" / "icons"
    src_toolbar_light_icon = assets_dir / "toolbar" / "icon_light.png"
    src_toolbar_dark_icon = assets_dir / "toolbar" / "icon_dark.png"
    src_pcm_icon = assets_dir / "pcm" / "icon.png"
    dist_root = repo_root / "dist"
    archive_root = dist_root / "pcb2fritzing-pcm"

    if args.release:
        _validate_release_assets(src_toolbar_light_icon, src_toolbar_dark_icon, src_pcm_icon)

    # ---- (re)build the exploded archive directory --------------------------
    if archive_root.exists():
        shutil.rmtree(archive_root)
    archive_root.mkdir(parents=True)

    # plugins/ – top-level entry + package
    plugins_dir = archive_root / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "__init__.py").write_text(_PLUGIN_ENTRY, encoding="utf-8")
    # Use lowercase action filename for stable behavior across platforms.
    (plugins_dir / "pcb2fritzing_action.py").write_text(_PLUGIN_ENTRY, encoding="utf-8")
    _copy_package(src_pkg, plugins_dir / "pcb2fritzing")

    # resources/ – optional PCM catalog icon (64x64 recommended)
    resources_dir = archive_root / "resources"
    resources_dir.mkdir()
    if src_pcm_icon.exists():
        shutil.copy2(src_pcm_icon, resources_dir / "icon.png")
    else:
        print(f"ℹ  PCM icon not found at {src_pcm_icon}")
        print("   Add icon.png there to include a package icon in Plugin and Content Manager.")

    install_size = _dir_size_bytes(archive_root)

    # metadata.json packaged inside the archive (stable values only)
    archive_metadata = _build_archive_metadata()
    (archive_root / "metadata.json").write_text(
        json.dumps(archive_metadata, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )

    # ---- zip ---------------------------------------------------------------
    zip_path = dist_root / "PCB2FritzingPart-pcm.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=archive_root)

    # Separate registry metadata includes download/install fields tied to the zip.
    registry_metadata = _build_registry_metadata(zip_path, install_size)
    registry_metadata_path = dist_root / "metadata.registry.json"
    registry_metadata_path.write_text(
        json.dumps(registry_metadata, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Exploded archive : {archive_root}")
    print(f"PCM zip          : {zip_path}")
    print(f"Registry metadata: {registry_metadata_path}")
    print()
    if "YOURUSERNAME" in archive_metadata["identifier"]:
        print("⚠  TODO: update identity metadata in scripts/build_config.json")
        print("   required: username, author_name, and contact_web")
    version_info = registry_metadata["versions"][0]
    if (
        version_info["download_sha256"] == "YOUR_SHA256_HERE"
        or version_info["download_url"] == "<DOWNLOAD_URL>"
        or int(version_info["download_size"]) <= 0
    ):
        print("⚠  TODO: update release metadata in scripts/build_config.json")
        print("   required: download_sha256, download_size, and download_url")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
