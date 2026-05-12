from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kicad2fritzing.core.extractor import (
    map_model_to_fritzing_connectors,
    parse_kicad_board_to_model,
    validate_generated_artifacts,
    write_fritzing_part_fzp,
    write_placeholder_svg_views,
)


def _is_external_test_enabled() -> bool:
    return os.getenv("RUN_EXTERNAL_PROJECT_TESTS", "0") == "1"


def _config_path() -> Path:
    return Path(
        os.getenv(
            "K2F_EXTERNAL_PROJECTS_CONFIG",
            Path(__file__).with_name("external_projects.local.json"),
        )
    )


def _load_external_projects() -> list[dict]:
    config_file = _config_path()
    if not config_file.exists():
        return []

    payload = json.loads(config_file.read_text(encoding="utf-8"))
    projects = payload.get("projects", [])
    if not isinstance(projects, list):
        pytest.fail(f"Invalid external projects config: {config_file}")

    normalized: list[dict] = []
    for index, project in enumerate(projects):
        if not isinstance(project, dict):
            continue
        if not project.get("enabled", True):
            continue

        repo_url = str(project.get("repo_url", "")).strip()
        branch = str(project.get("branch", "")).strip()
        if not repo_url or not branch:
            pytest.fail(
                f"Invalid external project entry at index {index} in {config_file}: "
                "repo_url and branch are required"
            )

        normalized.append(
            {
                "name": str(project.get("name", repo_url)).strip() or repo_url,
                "repo_url": repo_url,
                "branch": branch,
                "repo_subdir": str(project.get("repo_subdir", "")).strip(),
                "board_rel_path": str(project.get("board_rel_path", "")).strip(),
            }
        )

    return normalized


def _to_https_if_github_ssh(repo_url: str) -> str:
    # Allow transparent fallback for environments without SSH keys configured.
    if repo_url.startswith("git@github.com:"):
        return "https://github.com/" + repo_url[len("git@github.com:") :]
    return repo_url


def _clone_repo(repo_url: str, branch: str, dest_dir: Path) -> tuple[bool, str]:
    cmd = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--branch",
        branch,
        repo_url,
        str(dest_dir),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode == 0:
        return True, ""
    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    return False, f"{stdout}\n{stderr}".strip()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "external_project" in metafunc.fixturenames:
        projects = _load_external_projects()
        if not projects:
            metafunc.parametrize(
                "external_project",
                [pytest.param({}, marks=pytest.mark.skip(reason="No enabled external projects configured"))],
            )
            return

        metafunc.parametrize(
            "external_project",
            [pytest.param(project, id=project["name"]) for project in projects],
        )


def test_external_kicad_repo_pipeline(tmp_path: Path, external_project: dict) -> None:
    if not _is_external_test_enabled():
        pytest.skip("Set RUN_EXTERNAL_PROJECT_TESTS=1 to run external integration tests")

    if shutil.which("git") is None:
        pytest.skip("git executable not available")

    repo_url = os.getenv("K2F_EXTERNAL_REPO_URL", external_project["repo_url"])
    branch = os.getenv("K2F_EXTERNAL_REPO_BRANCH", external_project["branch"])
    repo_subdir = os.getenv("K2F_EXTERNAL_REPO_SUBDIR", external_project["repo_subdir"]).strip()
    board_rel_path = os.getenv("K2F_EXTERNAL_BOARD_PATH", external_project["board_rel_path"]).strip()

    clone_root = tmp_path / "external_project"
    candidates = [repo_url]
    https_fallback = _to_https_if_github_ssh(repo_url)
    if https_fallback != repo_url:
        candidates.append(https_fallback)

    clone_errors: list[str] = []
    cloned = False
    for candidate in candidates:
        ok, err = _clone_repo(candidate, branch, clone_root)
        if ok:
            cloned = True
            break
        clone_errors.append(f"[{candidate}] {err}")

    if not cloned:
        pytest.skip("Could not clone external repo:\n" + "\n".join(clone_errors))

    search_root = clone_root / repo_subdir if repo_subdir else clone_root
    if not search_root.exists():
        pytest.fail(f"Configured K2F_EXTERNAL_REPO_SUBDIR not found: {search_root}")

    if board_rel_path:
        board_file = search_root / board_rel_path
        if not board_file.exists():
            pytest.fail(f"Configured K2F_EXTERNAL_BOARD_PATH not found: {board_file}")
    else:
        board_candidates = sorted(search_root.rglob("*.kicad_pcb"))
        if not board_candidates:
            pytest.fail(f"No .kicad_pcb files found under: {search_root}")
        board_file = board_candidates[0]

    board_model = parse_kicad_board_to_model(board_file)
    assert board_model.get("source_board")
    assert isinstance(board_model.get("footprints"), list)
    assert isinstance(board_model.get("nets"), list)

    connector_model = map_model_to_fritzing_connectors(board_model)
    assert "connector_count" in connector_model

    out_dir = tmp_path / "generated"
    fzp_path = write_fritzing_part_fzp(connector_model, out_dir)
    svg_paths = write_placeholder_svg_views(connector_model, out_dir, board_model=board_model)

    assert fzp_path.exists()
    assert len(svg_paths) == 4
    for path in svg_paths:
        assert path.exists()

    print(f"Generated Fritzing part files at: {out_dir}")

    validation = validate_generated_artifacts(connector_model, out_dir)
    assert validation["is_valid"], json.dumps(validation, indent=2)
