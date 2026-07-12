"""進捗イベントの機械可読出力（JSON Lines）。対話フロント連携用（#32）。

各段・各ページの進捗を「1 行 1 イベント」の JSON で標準出力へ流すための最小の
発火機構。既定シンクは no-op（emit しても何も出さない）なので、ライブラリ利用や
既存テストの挙動は一切変わらない。CLI が `--progress json` のときだけ JSON Lines
シンクを差し込む。人間向けの INFO ログ（標準エラー）とは出力先が独立し両立する。

Why: フロント（`npx kindle2pdf`）はスピナー／進捗バーに反映するため、段名・ページ
番号・総数・完了・エラーを機械可読で受け取る必要がある（Issue #32）。段側は
`emit(...)` を呼ぶだけで、出力形式・出力先はシンク側が決める（関心の分離）。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from contextlib import contextmanager
from typing import IO


def _noop(event: dict) -> None:
    """既定シンク。進捗を破棄する（人間向けログ経路とは独立）。"""


# 現在のシンク。emit はこれに dict を渡すだけ。既定は破棄（no-op）。
_sink: Callable[[dict], None] = _noop


def emit(event: str, **fields: object) -> None:
    """1 進捗イベントを現在のシンクに渡す。既定シンクでは何も出さない。

    event はイベント種別（"stage_start" / "page" / "complete" / "error" 等）。
    fields は段名・ページ番号・総数など任意の付随情報。
    """
    payload: dict = {"event": event}
    payload.update(fields)
    _sink(payload)


def set_sink(sink: Callable[[dict], None] | None) -> None:
    """シンクを差し替える（None で no-op に戻す）。テスト・CLI 用の注入口。"""
    global _sink
    _sink = sink if sink is not None else _noop


@contextmanager
def json_lines(stream: IO[str] | None = None):
    """with 内の emit を JSON Lines として stream（既定: stdout）へ書くシンクを有効化する。

    抜けるときに直前のシンクへ確実に戻す（ネスト・例外時も元に戻る）。
    """
    out = stream if stream is not None else sys.stdout

    def _write(event: dict) -> None:
        out.write(json.dumps(event, ensure_ascii=False) + "\n")
        out.flush()

    prev = _sink
    set_sink(_write)
    try:
        yield
    finally:
        set_sink(prev)
