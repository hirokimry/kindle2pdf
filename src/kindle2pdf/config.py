"""YAML設定の読込・検証。dataclass で型を明示する。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class CaptureConfig:
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
    # 撮影の最大ページ数。0 = 上限なし（最終ページの自動検出で止まる）。撮影は pHash 終端
    # 検出が実質の停止機構で、正の値は安全上限として効く（capture 側に高い内部安全弁あり）。
    max_pages: int = 0
    prevent_sleep: bool = True


@dataclass
class PreprocessConfig:
    trim: dict = field(
        default_factory=lambda: {"top": 0.11, "bottom": 0.035, "left": 0.015, "right": 0.015}
    )
    min_brightness: int = 20


@dataclass
class OcrConfig:
    # OCR エンジン。apple=Apple Vision（既定・端末内完結・無料・macOS 専用）/
    # google=Google Cloud Vision（クラウド送信・従量課金・手描き/崩し字に強い、Issue #56）。
    # 既定を apple に据えることで既存利用者の挙動を変えない。languages / recognition_level は
    # apple 専用パラメータ（google は言語ヒントを内部で ja/en 固定にする）。
    engine: str = "apple"
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
        # 静的 region フォールバックは廃止された（Issue #47）。auto_region による自動検出に
        # 一本化されたため、既存 config.yaml に残る region / auto_region キーは CaptureConfig に
        # 渡す前に取り除き未知キー TypeError を防ぐ。auto_region: false を明示していたユーザーに
        # だけ撮影方式が auto へ変わることを warning で知らせる（true・未記載はサイレント無視）。
        capture_raw = dict(raw.get("capture") or {})
        if capture_raw.pop("auto_region", None) is False:
            logger.warning(
                "capture.auto_region: false は廃止されました。"
                "静的 region 撮影は無くなり、常に Kindle ウィンドウ自動検出で撮影します。"
            )
        capture_raw.pop("region", None)
        return cls(
            book_title=raw.get("book_title", "sample-book"),
            capture=CaptureConfig(**capture_raw),
            preprocess=PreprocessConfig(**(raw.get("preprocess") or {})),
            ocr=OcrConfig(**(raw.get("ocr") or {})),
            build=BuildConfig(**(raw.get("build") or {})),
        )

    def validate(self) -> None:
        """最低限の妥当性検証。book_title・page_turn_key・reading_order などをここで弾く。"""
        # book_title は work/<book_title>/<日時>/ のディレクトリ名になる。パス区切りや
        # 相対参照を含むと work/ の外に run ディレクトリが作られ既存ファイルを上書きしうる。
        # --title でフロント（npx kindle2pdf）の回答が直接渡るため、想定外タイトルを明確な
        # エラーで弾く（誤入力・"/" を含む書名対策。Issue #32）。
        title = self.book_title
        if (
            not title
            or "/" in title
            or "\\" in title
            or "\x00" in title
            or title in (".", "..")
        ):
            raise ValueError(
                "book_title にパス区切り（/ \\）・相対参照（. ..）・空文字は使えません。"
                "work/<book_title>/ のフォルダ名になるため、書名にこれらが含まれる場合は "
                "「_」等へ置き換えてください。"
            )
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
        # engine は apple（端末内 Apple Vision）/ google（クラウド Google Cloud Vision）のみ。
        # google 選択時の鍵（GOOGLE_VISION_API_KEY）不在は撮影前の validate では弾かず、
        # OCR 段の開始時に明確なエラーで止める（ocr.ocr_all）。
        if self.ocr.engine not in ("apple", "google"):
            raise ValueError("ocr.engine は apple / google のいずれか。")
        fmt = self.build.image_format.lower()
        if fmt not in ("jpeg", "jpg", "png"):
            raise ValueError("build.image_format は jpeg / png のいずれか。")
        # JPEG 品質は 1〜100（Pillow の許容範囲）。PNG は可逆なので品質検証を課さない。
        if fmt in ("jpeg", "jpg") and not 1 <= self.build.jpeg_quality <= 100:
            raise ValueError("build.jpeg_quality は 1〜100 の範囲で指定してください。")
        # max_pages は 0=上限なし / 正=安全上限。負値は意味を持たないため弾く。
        if self.capture.max_pages < 0:
            raise ValueError("capture.max_pages は 0（上限なし）以上で指定してください。")
