"""run コマンドの対話フロント連携フラグの単体テスト（#32）。

pipeline.run / _open_file を monkeypatch し、Kindle・osascript・open 実機に依存せず
「フラグ→Config 反映」「--progress json の JSON Lines 出力」「完了時の自動オープンと
--no-open 抑制」を検証する。
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from kindle2pdf import cli
from kindle2pdf import pipeline as pipeline_mod
from kindle2pdf import progress as progress_mod
from kindle2pdf.cli import main


def _write_config(tmp_path: Path, extra: str = "") -> None:
    (tmp_path / "config.yaml").write_text(
        "book_title: base-title\ncapture:\n"
        '  app_name: "Kindle"\n'
        + extra,
        encoding="utf-8",
    )


def _stub_pipeline(monkeypatch, recorder: dict, *, emit_events: bool = False):
    """pipeline.run を差し替え、受け取った cfg を記録して run_dir を返すスタブにする。"""

    def fake_run(cfg, *, run_dir=None, resume=True, now=None):
        recorder["cfg"] = cfg
        if emit_events:
            # --progress json のシンクが CLI 側で有効化されている前提で発火する。
            progress_mod.emit("stage_start", stage="capture")
            progress_mod.emit("complete", output="work/base-title/x/output/base-title.pdf")
        return Path("work") / cfg.book_title / "2026-07-12_000000"

    monkeypatch.setattr(pipeline_mod, "run", fake_run)


def test_run_title_flag_overrides_book_title(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)
    rec: dict = {}
    _stub_pipeline(monkeypatch, rec)
    monkeypatch.setattr(cli, "_open_file", lambda path: None)

    result = CliRunner().invoke(main, ["run", "--config", "config.yaml", "--title", "my-book"])

    assert result.exit_code == 0, result.output
    assert rec["cfg"].book_title == "my-book"  # フラグが config を上書き


def test_run_without_title_keeps_config_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)
    rec: dict = {}
    _stub_pipeline(monkeypatch, rec)
    monkeypatch.setattr(cli, "_open_file", lambda path: None)

    result = CliRunner().invoke(main, ["run", "--config", "config.yaml"])

    assert result.exit_code == 0, result.output
    assert rec["cfg"].book_title == "base-title"  # 未指定なら config 値を尊重（--config 経路維持）


def test_run_reading_order_flag_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)
    rec: dict = {}
    _stub_pipeline(monkeypatch, rec)
    monkeypatch.setattr(cli, "_open_file", lambda path: None)

    result = CliRunner().invoke(
        main, ["run", "--config", "config.yaml", "--reading-order", "ltr"]
    )

    assert result.exit_code == 0, result.output
    assert rec["cfg"].ocr.reading_order == "ltr"


def test_run_auto_opens_pdf_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)
    _stub_pipeline(monkeypatch, {})
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_file", lambda path: opened.append(path))

    result = CliRunner().invoke(main, ["run", "--config", "config.yaml", "--title", "t"])

    assert result.exit_code == 0, result.output
    assert opened == [str(Path("work") / "t" / "2026-07-12_000000" / "output" / "t.pdf")]


def test_run_no_open_suppresses_auto_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)
    _stub_pipeline(monkeypatch, {})
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_file", lambda path: opened.append(path))

    result = CliRunner().invoke(main, ["run", "--config", "config.yaml", "--no-open"])

    assert result.exit_code == 0, result.output
    assert opened == []  # --no-open で自動オープンしない


def test_run_progress_json_emits_json_lines(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)
    _stub_pipeline(monkeypatch, {}, emit_events=True)
    monkeypatch.setattr(cli, "_open_file", lambda path: None)

    result = CliRunner().invoke(
        main, ["run", "--config", "config.yaml", "--progress", "json", "--no-open"]
    )

    assert result.exit_code == 0, result.output
    import json

    json_lines = [
        line for line in result.output.splitlines() if line.startswith("{")
    ]
    events = [json.loads(line) for line in json_lines]
    kinds = [e["event"] for e in events]
    assert "stage_start" in kinds
    assert "complete" in kinds
    # 各行が単独で JSON として妥当（1 行 1 イベント）。
    assert events[0]["event"] == "stage_start"


def test_run_rejects_unsafe_title_with_friendly_error(tmp_path, monkeypatch):
    """--title にパス区切りを渡すと生 traceback でなく明確なエラーで止まる（#32）。

    pipeline.run はスタブせず実物を通す（Config.validate が撮影前に book_title を弾く）。
    validate は純粋関数で Kindle 実機に依存しない。
    """
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_file", lambda path: opened.append(path))

    result = CliRunner().invoke(
        main, ["run", "--config", "config.yaml", "--title", "../escape"]
    )

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "book_title" in result.output  # ClickException 経由の整形メッセージ
    assert opened == []  # 失敗時は自動オープンしない


def test_calibrate_rejects_unsafe_book_title(tmp_path, monkeypatch):
    """calibrate も book_dir(cfg) 作成前に book_title を検証し work/ 外流出を防ぐ（#32）。

    calibrate は run/capture と違い pipeline.run を経由しないため、コマンド側で
    cfg.validate() を呼んで同じ検証を効かせる。grab は monkeypatch で実撮影を避ける。
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "book_title: ../escape\ncapture:\n"
        '  app_name: "Kindle"\n',
        encoding="utf-8",
    )
    from kindle2pdf import capture as capture_mod

    called = {"grab": False}
    monkeypatch.setattr(
        capture_mod, "grab", lambda *a, **k: called.__setitem__("grab", True)
    )

    result = CliRunner().invoke(main, ["calibrate", "--config", "config.yaml"])

    assert result.exit_code != 0
    assert "book_title" in result.output  # book_title 検証で撮影前に止まる
    assert called["grab"] is False  # work/ 外に書き込む前に弾かれる


def test_run_default_progress_text_emits_no_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path)
    _stub_pipeline(monkeypatch, {}, emit_events=True)
    monkeypatch.setattr(cli, "_open_file", lambda path: None)

    result = CliRunner().invoke(main, ["run", "--config", "config.yaml", "--no-open"])

    assert result.exit_code == 0, result.output
    # text モードでは JSON Lines を出さない（従来ログ経路のみ。emit は no-op シンク）。
    assert not any(line.startswith('{"event"') for line in result.output.splitlines())
