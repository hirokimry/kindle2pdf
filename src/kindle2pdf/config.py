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
    # ウィンドウを自動検出して撮影領域に使うか。true なら静的 region を無視し、毎回 Kindle
    # ウィンドウを検出して `-l` で撮り、AX 実測の macOS タイトルバー帯だけを上端クロップする
    # （本文の白余白・柱は一切削らない）。Kindle 自身の進捗フッター等は Kindle の表示設定で
    # 消す運用。false で静的 region。通常ウィンドウ表示前提（全画面は自動ページ送り不可）。
    auto_region: bool = True
    # Kindle アプリの AppleScript / ウィンドウ名。空なら実行時に候補（"Amazon Kindle" →
    # "Kindle"）を順に試して自動検出しキャッシュする（#33）。環境により名前が違う
    # （新しめの Mac 版は "Amazon Kindle"、`tell application "Kindle"` が -1728 で失敗する
    # 環境もある）ため、明示指定があればそれを最優先する。
    app_name: str = ""
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
    min_brightness: int = 20


@dataclass
class OcrConfig:
    languages: list[str] = field(default_factory=lambda: ["ja-JP", "en-US"])
    recognition_level: str = "accurate"
    # 見開き（2カラム）ページを列認識で読み順に並べる方向。
    # rtl=右列→左列（漫画・縦書きの見開き） / ltr=左列→右列（横書き）。
    # 片ページ（単一カラム）では方向は結果に影響しない（上→下ソートのみ）。
    reading_order: str = "rtl"


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
        # 見開き左右分割は廃止された（Issue #29）。見開き/片ページは Kindle のウィンドウ幅で
        # 選ぶ運用に変わり、撮影は常に「ウィンドウ中身をそのまま 1 撮影 = 1 ページ」で撮る。
        # 廃止キー spread_mode / split_spread が残っていたら、サイレントに挙動を変えず
        # cryptic な TypeError でもなく、移行を促す明確なエラーで弾く。
        _migration = (
            "見開きの左右分割は廃止され、1 撮影 = 1 ページになりました。"
            "見開き/片ページは Kindle のウィンドウ幅で選びます"
            "（半画面幅=片ページ / 全画面幅=見開き 1 枚）。"
        )
        if isinstance(raw.get("capture"), dict) and "spread_mode" in raw["capture"]:
            raise ValueError(
                "capture.spread_mode は廃止されました。" + _migration
                + "config.yaml の capture.spread_mode 行を削除してください。"
            )
        if isinstance(raw.get("preprocess"), dict) and "split_spread" in raw["preprocess"]:
            raise ValueError(
                "preprocess.split_spread は廃止されました。" + _migration
                + "config.yaml の preprocess.split_spread 行を削除してください。"
            )
        return cls(
            book_title=raw.get("book_title", "sample-book"),
            capture=CaptureConfig(**(raw.get("capture") or {})),
            preprocess=PreprocessConfig(**(raw.get("preprocess") or {})),
            ocr=OcrConfig(**(raw.get("ocr") or {})),
            build=BuildConfig(**(raw.get("build") or {})),
        )

    def validate(self) -> None:
        """最低限の妥当性検証。region 未実測などをここで弾く。"""
        # auto_region 時は実行時にウィンドウから領域を算出するため静的 region 検証は不要。
        if not self.capture.auto_region:
            validate_region(self.capture.region)
        if self.capture.page_turn_key not in ("right", "left"):
            raise ValueError("capture.page_turn_key は right / left のいずれか。")
        # reading_order は列認識の読み順方向。旧値 split / column（分割前提のデッド定義）は
        # 分割廃止で意味を失ったため、移行を促す明確なエラーで弾く。
        if self.ocr.reading_order not in ("rtl", "ltr"):
            if self.ocr.reading_order in ("split", "column"):
                raise ValueError(
                    "ocr.reading_order の split / column は廃止されました。"
                    "見開き（2カラム）の読み順方向を rtl（右→左・漫画/縦書き）"
                    "または ltr（左→右・横書き）で指定してください。"
                )
            raise ValueError("ocr.reading_order は rtl / ltr のいずれか。")
        fmt = self.build.image_format.lower()
        if fmt not in ("jpeg", "jpg", "png"):
            raise ValueError("build.image_format は jpeg / png のいずれか。")
        # JPEG 品質は 1〜100（Pillow の許容範囲）。PNG は可逆なので品質検証を課さない。
        if fmt in ("jpeg", "jpg") and not 1 <= self.build.jpeg_quality <= 100:
            raise ValueError("build.jpeg_quality は 1〜100 の範囲で指定してください。")
