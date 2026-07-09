"""state.json の読み書き・レジューム制御。

各段は「処理→state更新」を1ページ単位で逐次コミットし、
再実行時は未完了ページから続行する。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

STAGES = ("capture", "preprocess", "ocr", "build", "done")


@dataclass
class State:
    book_title: str = "sample-book"
    stage: str = "capture"       # capture|preprocess|ocr|build|done
    captured: int = 0            # raw/ に確定済みの撮影枚数
    last_hash: str = ""          # 直近確定フレームのpHash
    repeat_count: int = 0        # 同一ハッシュ連続回数
    pages_total: int = 0         # 分割後の確定ページ数（preprocess後に確定）
    ocr_done: int = 0            # OCR完了ページ数
    updated_at: str = ""         # 実行後にスタンプ（呼び出し側で設定）
    hash_history: list[str] = field(default_factory=list)

    # --- 永続化 ---
    @classmethod
    def load(cls, path: str | Path) -> "State":
        p = Path(path)
        if not p.exists():
            return cls()
        return cls(**json.loads(p.read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def advance_stage(self) -> None:
        """次の段へ遷移する。"""
        idx = STAGES.index(self.stage)
        self.stage = STAGES[min(idx + 1, len(STAGES) - 1)]
