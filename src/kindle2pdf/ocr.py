"""ocr 段 — Apple Vision OCR ラッパ（ocrmac）。

PoC根拠: Visionは余分な空白がほぼ無く（0.01/字）、日本語の語句検索が壊れない。
分割済みページは単一カラムなので読み順は「yの大きい順（＝上から）」で単純ソート。

ocrmac は macOS 専用（extra: macos）。import は関数内で遅延させ、
非mac環境（CI ubuntu 等）でモジュール import 自体は失敗しないようにする。

実装チケット: P5(Vision OCR)
"""

from __future__ import annotations

from pathlib import Path

from .config import Config

# 返り値要素: (text, confidence, [x, y, w, h])  座標は正規化(0..1)・原点左下
OcrItem = tuple[str, float, list[float]]


def ocr_page(path: str | Path, cfg: Config) -> list[OcrItem]:
    """1ページを Vision OCR して (text, confidence, bbox) のリストを返す。"""
    from ocrmac import ocrmac  # 遅延import（macOS専用）

    result = ocrmac.OCR(
        str(path),
        language_preference=cfg.ocr.languages,
        recognition_level=cfg.ocr.recognition_level,
    ).recognize()
    # ocrmac の返り値 (text, confidence, bbox) をそのまま整形して返す
    return [(text, conf, list(bbox)) for text, conf, bbox in result]


def ocr_all(cfg: Config, state: State=None) -> None:  # type: ignore[assignment]
    """pages/ の全ページをOCRし ocr/page_XXXX.json に保存する。"""
    raise NotImplementedError("P5: pages/→ocr/ のバッチ処理とJSON保存を実装する")
