"""progress（進捗イベント発火）の単体テスト（#32）。

既定シンクが no-op であること・JSON Lines シンクが行を書き出すこと・
with を抜けたら直前のシンクへ確実に戻ることを検証する。実機依存なし。
"""

from __future__ import annotations

import io
import json

from kindle2pdf import progress


def test_emit_is_noop_by_default():
    # 既定シンクでは emit しても例外なく何も起きない（ライブラリ利用・既存挙動を壊さない）。
    progress.set_sink(None)
    progress.emit("page", stage="ocr", page=1, total=3)  # 例外が出なければ OK


def test_set_sink_receives_events():
    got: list[dict] = []
    progress.set_sink(got.append)
    try:
        progress.emit("stage_start", stage="capture")
        progress.emit("page", stage="capture", page=2, total=None)
    finally:
        progress.set_sink(None)

    assert got == [
        {"event": "stage_start", "stage": "capture"},
        {"event": "page", "stage": "capture", "page": 2, "total": None},
    ]


def test_json_lines_writes_one_event_per_line():
    buf = io.StringIO()
    with progress.json_lines(buf):
        progress.emit("stage_start", stage="ocr")
        progress.emit("complete", output="work/x/output/x.pdf")

    lines = buf.getvalue().splitlines()
    assert len(lines) == 2  # 1 行 1 イベント
    assert json.loads(lines[0]) == {"event": "stage_start", "stage": "ocr"}
    assert json.loads(lines[1]) == {"event": "complete", "output": "work/x/output/x.pdf"}


def test_json_lines_restores_previous_sink_on_exit():
    outer: list[dict] = []
    progress.set_sink(outer.append)
    try:
        with progress.json_lines(io.StringIO()):
            progress.emit("inner", n=1)  # JSON シンク側へ（outer には来ない）
        # with を抜けたら直前のシンク（outer）に戻る。
        progress.emit("after", n=2)
    finally:
        progress.set_sink(None)

    assert outer == [{"event": "after", "n": 2}]


def test_json_lines_restores_sink_even_on_exception():
    progress.set_sink(None)
    buf = io.StringIO()
    try:
        with progress.json_lines(buf):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    # 例外で抜けても no-op シンクに戻っている（以後の emit が buf を汚さない）。
    progress.emit("after", n=1)
    assert "after" not in buf.getvalue()
