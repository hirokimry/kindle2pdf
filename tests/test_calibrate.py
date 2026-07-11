"""calibrate（P1）の単体テスト。

screencapture を monkeypatch で差し替えるため macOS 依存なしで動く。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from kindle2pdf import capture as capture_mod
from kindle2pdf.cli import main
from kindle2pdf.config import Config, validate_region


def _cfg(region: list) -> Config:
    cfg = Config()
    cfg.capture.auto_region = False  # 静的 region 経路を検証する
    cfg.capture.region = region
    return cfg


def test_validate_region_normalizes_valid():
    assert validate_region([10, 20, 300, 400]) == (10, 20, 300, 400)


def test_validate_region_allows_negative_origin():
    # マルチモニタでは x / y が負値を取り得る（幅高さが正なら許容）。
    assert validate_region([-100, -50, 800, 600]) == (-100, -50, 800, 600)


@pytest.mark.parametrize("bad", [[0, 0, 0, 0], [10, 20, 0, 400], [10, 20, 300, -1]])
def test_validate_region_rejects_nonpositive_size(bad):
    with pytest.raises(ValueError):
        validate_region(bad)


@pytest.mark.parametrize("bad", [[1, 2, 3], [1, 2, 3, 4, 5], "1,2,3,4"])
def test_validate_region_rejects_wrong_shape(bad):
    with pytest.raises(ValueError):
        validate_region(bad)


def test_validate_region_rejects_non_int():
    with pytest.raises(ValueError):
        validate_region([1, 2, "x", 4])


def test_run_calibrate_saves_and_returns_path(tmp_path, monkeypatch):
    captured = {}

    def fake_grab(region, out_path):
        captured["region"] = region
        Path(out_path).write_bytes(b"png")
        return str(out_path)

    monkeypatch.setattr(capture_mod, "grab", fake_grab)

    out, region = capture_mod.run_calibrate(_cfg([5, 6, 700, 800]), tmp_path / "work")

    assert out == tmp_path / "work" / "calibrate.png"
    assert out.exists()
    assert region == (5, 6, 700, 800)
    assert captured["region"] == [5, 6, 700, 800]


def test_run_calibrate_returns_normalized_region(tmp_path, monkeypatch):
    # config の生の float 値ではなく int 正規化後の region を返す。
    captured = {}
    monkeypatch.setattr(
        capture_mod,
        "grab",
        lambda region, out_path: captured.update(region=region)
        or Path(out_path).write_bytes(b"x"),
    )

    _out, region = capture_mod.run_calibrate(_cfg([10.5, 20, 300, 400]), tmp_path / "w")

    assert region == (10, 20, 300, 400)
    assert captured["region"] == [10, 20, 300, 400]


def test_run_calibrate_rejects_unmeasured_region(tmp_path, monkeypatch):
    called = {"grab": False}
    monkeypatch.setattr(
        capture_mod, "grab", lambda *a, **k: called.__setitem__("grab", True)
    )
    with pytest.raises(ValueError):
        capture_mod.run_calibrate(_cfg([0, 0, 0, 0]), tmp_path / "work")
    assert called["grab"] is False


def test_cli_calibrate_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 生の値が float でも表示は int 正規化後の region になることを確認する。
    (tmp_path / "config.yaml").write_text(
        "book_title: t\ncapture:\n  auto_region: false\n  region: [1.9, 2, 300, 400]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        capture_mod, "grab", lambda region, out_path: Path(out_path).write_bytes(b"x")
    )

    result = CliRunner().invoke(main, ["calibrate", "--config", "config.yaml"])

    assert result.exit_code == 0, result.output
    assert "保存先" in result.output
    assert "[1, 2, 300, 400]" in result.output
    assert (tmp_path / "work" / "t" / "calibrate.png").exists()


def test_cli_calibrate_invalid_region_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "book_title: t\ncapture:\n  auto_region: false\n  region: [0, 0, 0, 0]\n",
        encoding="utf-8",
    )

    def boom(*a, **k):
        raise AssertionError("region 不正時は grab を呼んではならない")

    monkeypatch.setattr(capture_mod, "grab", boom)

    result = CliRunner().invoke(main, ["calibrate", "--config", "config.yaml"])

    assert result.exit_code != 0
    assert "region" in result.output
