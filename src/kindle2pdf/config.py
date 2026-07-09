"""YAML設定の読込・検証。dataclass で型を明示する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CaptureConfig:
    region: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    spread_mode: bool = True
    page_turn_key: str = "right"
    page_turn_method: str = "osascript"
    page_turn_wait: float = 1.0
    stable_wait: float = 0.3
    stable_required: int = 2
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
        x, y, w, h = self.capture.region
        if w <= 0 or h <= 0:
            raise ValueError(
                "capture.region が未実測です。`kindle2pdf calibrate` で [x,y,w,h] を確定してください。"
            )
        if self.capture.page_turn_key not in ("right", "left"):
            raise ValueError("capture.page_turn_key は right / left のいずれか。")
