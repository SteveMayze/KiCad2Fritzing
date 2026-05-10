from __future__ import annotations

import shutil
from pathlib import Path


def _copy_package(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)

    def ignore_filter(_dir: str, names: list[str]) -> set[str]:
        ignored = {"__pycache__"}
        ignored.update({name for name in names if name.endswith(".pyc")})
        return ignored

    shutil.copytree(src, dst, ignore=ignore_filter)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    src_pkg = repo_root / "src" / "kicad2fritzing"
    dist_root = repo_root / "dist"
    kicad_dist = dist_root / "kicad10-action-plugin"
    plugin_root = kicad_dist / "KiCad2Fritzing"

    if kicad_dist.exists():
        shutil.rmtree(kicad_dist)
    kicad_dist.mkdir(parents=True, exist_ok=True)
    plugin_root.mkdir(parents=True, exist_ok=True)

    plugin_entry = plugin_root / "KiCad2Fritzing.py"
    plugin_entry.write_text(
        "\n".join(
            [
                '"""KiCad 10 Action Plugin entrypoint for KiCad2Fritzing."""',
                "",
                "from __future__ import annotations",
                "",
                "import sys",
                "from pathlib import Path",
                "",
                "PLUGIN_DIR = Path(__file__).resolve().parent",
                "if str(PLUGIN_DIR) not in sys.path:",
                "    sys.path.insert(0, str(PLUGIN_DIR))",
                "",
                "from kicad2fritzing.kicad.plugin import register_plugin",
                "",
                "register_plugin()",
                "",
            ]
        ),
        encoding="utf-8",
    )

    _copy_package(src_pkg, plugin_root / "kicad2fritzing")

    (plugin_root / "README.txt").write_text(
        "KiCad2Fritzing Action Plugin for KiCad 10.\\n"
        "Copy this KiCad2Fritzing folder into KiCad's scripting/plugins directory.\\n",
        encoding="utf-8",
    )

    zip_base = dist_root / "KiCad2Fritzing-kicad10-action-plugin"
    if (zip_base.with_suffix(".zip")).exists():
        (zip_base.with_suffix(".zip")).unlink()

    shutil.make_archive(str(zip_base), "zip", root_dir=kicad_dist, base_dir="KiCad2Fritzing")

    print(f"Built plugin directory: {plugin_root}")
    print(f"Built plugin zip: {zip_base.with_suffix('.zip')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
