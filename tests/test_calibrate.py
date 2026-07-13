"""calibrate（P1）の単体テスト。

screencapture を monkeypatch で差し替えるため macOS 依存なしで動く。
"""

from __future__ import annotations

from click.testing import CliRunner

from kindle2pdf import capture as capture_mod
from kindle2pdf.cli import main


def test_cli_calibrate_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # calibrate は auto_region で Kindle ウィンドウを自動検出して撮る。run_calibrate を
    # 差し替え、CLI 層の整形（保存先表示・正規化済み region 表示）だけを検証する。
    (tmp_path / "config.yaml").write_text("book_title: t\n", encoding="utf-8")

    def fake_run_calibrate(cfg, work_dir):
        out = work_dir / "calibrate.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return out, (1, 2, 300, 400)

    monkeypatch.setattr(capture_mod, "run_calibrate", fake_run_calibrate)

    result = CliRunner().invoke(main, ["calibrate", "--config", "config.yaml"])

    assert result.exit_code == 0, result.output
    assert "保存先" in result.output
    assert "[1, 2, 300, 400]" in result.output
    assert (tmp_path / "work" / "t" / "calibrate.png").exists()


def test_cli_calibrate_auto_region_runtimeerror_is_friendly(tmp_path, monkeypatch):
    """auto_region の検出失敗(RuntimeError)を生 traceback でなく明確なエラーで返す。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "book_title: t\n", encoding="utf-8"
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
        "book_title: t\n", encoding="utf-8"
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
        "book_title: t\n", encoding="utf-8"
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
        "book_title: t\n", encoding="utf-8"
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
        "book_title: t\ncapture:\n  app_name: \"Kindle\"\n", encoding="utf-8"
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


