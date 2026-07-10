"""YAML設定の読込・検証。dataclass で型を明示する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


def validate_region(region: list[int]) -> tuple[int, int, int, int]:
    """capture.region を [x, y, width, height] として検証し正規化する。

    calibrate / capture が撮影前に共通で使う単一の検証点。未設定の
    [0, 0, 0, 0] や要素数・型の不正、幅高さ 0 以下を明確な ValueError で弾く。
    x / y はマルチモニタで負値を取り得るため符号は問わない。
    """
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        raise ValueError(
            "capture.region は [x, y, width, height] の 4 要素で指定してください。"
        )
    try:
        x, y, w, h = (int(v) for v in region)
    except (TypeError, ValueError):
        raise ValueError("capture.region の各要素は整数で指定してください。") from None
    if w <= 0 or h <= 0:
        raise ValueError(
            "capture.region の width/height が 0 以下です（未実測の可能性）。"
            "`kindle2pdf calibrate` で読書領域 [x, y, w, h] を実測してください。"
        )
    return x, y, w, h


@dataclass
class CaptureConfig:
    region: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    spread_mode: bool = True
    # Kindle アプリの AppleScript 名。環境により "Amazon Kindle" 等になる
    # （`tell application "Kindle"` が -1728 で失敗する環境がある）。
    app_name: str = "Kindle"
    page_turn_key: str = "right"
    page_turn_method: str = "osascript"
    page_turn_wait: float = 1.0
    stable_wait: float = 0.3
    stable_required: int = 2
    stable_threshold: int = 2        # 安定確認用のpHash距離（同一フレーム判定）
    end_detect_repeats: int = 3
    same_threshold: int = 2
    max_pages: int = 3000
    prevent_sleep: bool = True


@dataclass
class PreprocessConfig:
    trim: dict = field(
        default_factory=lambda: {"top": 0.11, "bottom": 0.035, "left": 0.015, "right": 0.015}
    )
    split_spread: bool = True
    min_brightness: int = 20


@dataclass
class OcrConfig:
    languages: list[str] = field(default_factory=lambda: ["ja-JP", "en-US"])
    recognition_level: str = "accurate"
    reading_order: str = "split"


@dataclass
class BuildConfig:
    image_format: str = "jpeg"
    jpeg_quality: int = 88
    target_dpi: int = 300
    font: str = "HeiseiMin-W3"


@dataclass
class Config:
    book_title: str = "sample-book"
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    preprocess: PreprocessConfig = field(default_factory=PreprocessConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    build: BuildConfig = field(default_factory=BuildConfig)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        """config.yaml を読み込んで Config を構築する。未指定キーは既定値。"""
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(
            book_title=raw.get("book_title", "sample-book"),
            capture=CaptureConfig(**(raw.get("capture") or {})),
            preprocess=PreprocessConfig(**(raw.get("preprocess") or {})),
            ocr=OcrConfig(**(raw.get("ocr") or {})),
            build=BuildConfig(**(raw.get("build") or {})),
        )

    def validate(self) -> None:
        """最低限の妥当性検証。region 未実測などをここで弾く。"""
        validate_region(self.capture.region)
        if self.capture.page_turn_key not in ("right", "left"):
            raise ValueError("capture.page_turn_key は right / left のいずれか。")
        fmt = self.build.image_format.lower()
        if fmt not in ("jpeg", "jpg", "png"):
            raise ValueError("build.image_format は jpeg / png のいずれか。")
        # JPEG 品質は 1〜100（Pillow の許容範囲）。PNG は可逆なので品質検証を課さない。
        if fmt in ("jpeg", "jpg") and not 1 <= self.build.jpeg_quality <= 100:
            raise ValueError("build.jpeg_quality は 1〜100 の範囲で指定してください。")
