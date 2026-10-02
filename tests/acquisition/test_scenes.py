"""Tests for scene-selection handling and the upstream argument mapping.

These are pure unit tests: they do not import the upstream CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from acquisition import cli as wrapper_cli
from acquisition import scenes
from helpers import record_upstream_argv

DEFAULT_RAW_DIR = "data/raw"
DEFAULT_SCENES = "data/interim/selected-scenes.geojson"


def test_extract_uuids_from_feature_collection():
    payload = {
        "type": "FeatureCollection",
        "features": [
            {"id": "uuid-a", "properties": {"id": "uuid-a"}},
            {"id": "uuid-b", "properties": {"id": "uuid-b"}},
        ],
    }
    assert scenes.extract_uuids(payload) == ["uuid-a", "uuid-b"]


def test_extract_uuids_from_manifest_items():
    payload = {"items": [{"id": "x"}, {"uuid": "y"}]}
    assert scenes.extract_uuids(payload) == ["x", "y"]


def test_extract_uuids_returns_empty_when_no_identifier_present():
    assert scenes.extract_uuids({"type": "FeatureCollection", "features": [{}]}) == []


def test_load_scenes_rejects_payload_without_any_identifier(tmp_path):
    path = tmp_path / "selected-scenes.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [{}]}))
    with pytest.raises(ValueError):
        scenes.load_scenes(path)


def test_load_scenes_file_deduplicates_preserving_order(tmp_path):
    path = tmp_path / "selected-scenes.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [{"id": "a"}, {"id": "b"}, {"id": "a"}],
            }
        )
    )
    assert scenes.load_scenes(path) == ["a", "b"]


def test_scenes_file_with_no_items_is_an_error(tmp_path):
    path = tmp_path / "empty.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    with pytest.raises(ValueError):
        scenes.load_scenes(path)


def test_download_argv_for_scenes_file(tmp_path):
    path = tmp_path / "selected-scenes.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [{"id": "a"}]}))
    argv = scenes.download_argv(
        collection="Radarsat-2_Tropical_Forest_Products",
        scenes_path=path,
        uuids=[],
        output_dir=DEFAULT_RAW_DIR,
        env="prod",
        limit=100,
    )
    assert argv == [
        "download",
        "--collection",
        "Radarsat-2_Tropical_Forest_Products",
        "--input",
        str(path),
        "--dl_dir",
        DEFAULT_RAW_DIR,
        "--limit",
        "100",
        "--env",
        "prod",
    ]


def test_download_argv_for_explicit_uuids():
    argv = scenes.download_argv(
        collection="Radarsat-2_Tropical_Forest_Products",
        scenes_path=None,
        uuids=["a", "b"],
        output_dir=DEFAULT_RAW_DIR,
        env="prod",
        limit=100,
    )
    assert "--uuid" in argv
    assert argv[argv.index("--uuid") + 1] == "a,b"
    assert "--input" not in argv


def test_search_argv_uses_aoi_and_datetime():
    argv = scenes.search_argv(
        collection="Radarsat-2_Tropical_Forest_Products",
        aoi="aoi.geojson",
        bbox=None,
        datetime_range="2024-01-01/2024-01-31",
        limit=50,
        output="data/interim/search-results.geojson",
        filter_text=None,
        env="prod",
        anonymous=True,
    )
    assert argv[:2] == ["search", "--collection"]
    assert "--aoi" in argv
    assert "--anonymous" in argv
    assert argv[argv.index("--datetime") + 1] == "2024-01-01/2024-01-31"


# --------------------------------------------------------------------------
# end-to-end wrapper behaviour for the download path
# --------------------------------------------------------------------------


def _capture(monkeypatch):
    return record_upstream_argv(monkeypatch)


def _stub_prompts(monkeypatch):
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: "sentinel.user")
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: "sentinel-pass")


def test_download_defaults_to_raw_dir_and_default_scenes_path(
    monkeypatch, tmp_path, fake_aaa
):
    recorded = _capture(monkeypatch)
    _stub_prompts(monkeypatch)
    scenes_file = tmp_path / DEFAULT_SCENES
    scenes_file.parent.mkdir(parents=True, exist_ok=True)
    scenes_file.write_text(
        json.dumps({"type": "FeatureCollection", "features": [{"id": "uuid-1"}]})
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        wrapper_cli.main,
        ["download", "--collection", "Radarsat-2_Tropical_Forest_Products"],
    )
    assert result.exit_code == 0, result.output
    argv = recorded[-1]
    assert argv[argv.index("--dl_dir") + 1] == DEFAULT_RAW_DIR
    assert argv[argv.index("--input") + 1] == DEFAULT_SCENES
    assert (tmp_path / DEFAULT_SCENES).is_file()


def test_download_errors_when_no_selection_exists(monkeypatch, tmp_path, fake_aaa):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        wrapper_cli.main, ["download", "--collection", "Radarsat-2_Tropical_Forest_Products"]
    )
    assert result.exit_code != 0
    assert DEFAULT_SCENES in result.output


def test_download_accepts_uuid_list(monkeypatch, tmp_path, fake_aaa):
    recorded = _capture(monkeypatch)
    _stub_prompts(monkeypatch)
    result = CliRunner().invoke(
        wrapper_cli.main,
        [
            "download",
            "--collection",
            "Radarsat-2_Tropical_Forest_Products",
            "--uuid",
            "uuid-1",
            "--uuid",
            "uuid-2",
            "--output-dir",
            "somewhere/else",
        ],
    )
    assert result.exit_code == 0, result.output
    argv = recorded[-1]
    assert argv[argv.index("--uuid") + 1] == "uuid-1,uuid-2"
    assert argv[argv.index("--dl_dir") + 1] == "somewhere/else"


def test_pinned_constants_are_exact():
    assert wrapper_cli.EODMS_CLI_REV == "464b94920e7faf28c84a6d31229ef0b2828a1479"
    assert wrapper_cli.EODMS_CLI_REPO == "https://github.com/eodms-sgdot/eodms-cli.git"
    assert Path(wrapper_cli.UPSTREAM_SRC_DIR).name == "src"
