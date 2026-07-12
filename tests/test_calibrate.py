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
    cfg.capture.app_name = "Kindle"  # 明示指定で resolve_app_name を短絡（実 osascript を呼ばない）
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
    # app_name を明示して resolve_app_name を短絡し、実 osascript / Kindle 実機に依存させない。
    (tmp_path / "config.yaml").write_text(
        "book_title: t\ncapture:\n"
        '  app_name: "Kindle"\n  auto_region: false\n  region: [1.9, 2, 300, 400]\n',
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


def test_cli_calibrate_auto_region_runtimeerror_is_friendly(tmp_path, monkeypatch):
    """auto_region の検出失敗(RuntimeError)を生 traceback でなく明確なエラーで返す。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "book_title: t\ncapture:\n  auto_region: true\n", encoding="utf-8"
    )

    def boom(cfg, work_dir):
        # Kindle 未起動・アクセシビリティ権限未付与など初回失敗を模す。
        raise RuntimeError("AX で信号機ボタンを検出できませんでした")

    monkeypatch.setattr(capture_mod, "run_calibrate", boom)

    result = CliRunner().invoke(main, ["calibrate", "--config", "config.yaml"])

    assert result.exit_code != 0
    # ClickException 経由の整形メッセージであること（生 traceback でない）。
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "信号機ボタン" in result.output


def test_cli_run_runtimeerror_is_friendly(tmp_path, monkeypatch):
    """run コマンドも検出失敗(RuntimeError)を明確なエラーで返す（生 traceback にしない）。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "book_title: t\ncapture:\n  auto_region: true\n", encoding="utf-8"
    )
    import kindle2pdf.pipeline as pipeline_mod

    def boom(cfg, **kwargs):
        raise RuntimeError("Kindle ウィンドウが見つかりません")

    monkeypatch.setattr(pipeline_mod, "run", boom)

    result = CliRunner().invoke(main, ["run", "--config", "config.yaml"])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Kindle ウィンドウ" in result.output


def test_cli_run_resume_flag_reaches_pipeline(tmp_path, monkeypatch):
    """--resume/--no-resume が pipeline.run の resume 引数に届く（ウィザード再開拒否経路・#35）。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "book_title: t\ncapture:\n  auto_region: true\n", encoding="utf-8"
    )
    import kindle2pdf.pipeline as pipeline_mod

    captured = {}

    def fake_run(cfg, *, resume=True, **kwargs):
        captured["resume"] = resume
        return tmp_path / "work" / "t" / "2026-07-12_000000"

    monkeypatch.setattr(pipeline_mod, "run", fake_run)
    monkeypatch.setattr("kindle2pdf.cli._open_file", lambda p: None)

    CliRunner().invoke(main, ["run", "--config", "config.yaml", "--no-resume", "--no-open"])
    assert captured["resume"] is False  # --no-resume で新規 run を強制

    CliRunner().invoke(main, ["run", "--config", "config.yaml", "--no-open"])
    assert captured["resume"] is True  # 既定は再開


def test_cli_capture_runtimeerror_is_friendly(tmp_path, monkeypatch):
    """capture コマンドも検出失敗(RuntimeError)を明確なエラーで返す（生 traceback にしない）。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "book_title: t\ncapture:\n  auto_region: true\n", encoding="utf-8"
    )

    def boom(cfg, st, wd, state_path):
        raise RuntimeError("Quartz が利用できません")

    monkeypatch.setattr(capture_mod, "run_capture", boom)

    result = CliRunner().invoke(main, ["capture", "--config", "config.yaml"])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Quartz" in result.output


def test_cli_capture_failure_leaves_resumable_run_dir(tmp_path, monkeypatch):
    """capture 単体コマンドも state 保存前に落ちて空 run ディレクトリを積み上げない（#31）。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "book_title: t\ncapture:\n  auto_region: false\n  region: [0, 0, 400, 560]\n",
        encoding="utf-8",
    )
    import kindle2pdf.pipeline as pipeline_mod

    def boom(cfg, st, wd, state_path):
        # 最初の state.save 到達前にウィンドウ検出が失敗する初回撮影を模す。
        raise RuntimeError("Kindle ウィンドウが見つかりません")

    monkeypatch.setattr(capture_mod, "run_capture", boom)

    result1 = CliRunner().invoke(main, ["capture", "--config", "config.yaml"])
    assert result1.exit_code != 0

    from kindle2pdf.config import Config
    bdir = pipeline_mod.book_dir(Config(book_title="t"))
    first_dirs = pipeline_mod._run_dirs(bdir)
    assert len(first_dirs) == 1
    assert (first_dirs[0] / "state.json").exists()  # 初期 state が残っている

    # 2回目も同じ失敗。resume で同一ディレクトリを再開し新規を作らない。
    result2 = CliRunner().invoke(main, ["capture", "--config", "config.yaml"])
    assert result2.exit_code != 0
    assert pipeline_mod._run_dirs(bdir) == first_dirs  # ディレクトリが増えていない


def test_cli_config_load_error_is_friendly(tmp_path, monkeypatch):
    """config 読込時の廃止キー ValueError も生 traceback でなく明確なエラーで返す。"""
    monkeypatch.chdir(tmp_path)
    # 廃止キー split_spread は Config.load が ValueError を送出する。
    (tmp_path / "config.yaml").write_text(
        "book_title: t\npreprocess:\n  split_spread: true\n", encoding="utf-8"
    )

    result = CliRunner().invoke(main, ["calibrate", "--config", "config.yaml"])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "split_spread" in result.output


def test_cli_calibrate_invalid_region_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # app_name を明示して resolve_app_name を短絡し、実 osascript / Kindle 実機に依存させない。
    (tmp_path / "config.yaml").write_text(
        "book_title: t\ncapture:\n"
        '  app_name: "Kindle"\n  auto_region: false\n  region: [0, 0, 0, 0]\n',
        encoding="utf-8",
    )

    def boom(*a, **k):
        raise AssertionError("region 不正時は grab を呼んではならない")

    monkeypatch.setattr(capture_mod, "grab", boom)

    result = CliRunner().invoke(main, ["calibrate", "--config", "config.yaml"])

    assert result.exit_code != 0
    assert "region" in result.output
