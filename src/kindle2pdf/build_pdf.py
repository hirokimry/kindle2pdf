"""build 段 — 画像＋透明テキスト層で検索可能PDFを生成する（システムの肝）。

座標変換は **Y反転不要**（Vision・reportlab とも原点左下）。72dpi基準で px=pt 換算。
これ単体で「画像＋不可視テキスト層」の検索可能PDFが完成する（ocrmypdf不要）。

実装チケット: P6(透明テキスト層PDF)
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image
from reportlab import rl_config
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from .config import Config

logger = logging.getLogger(__name__)

# ocr.py と同じ (text, confidence, [x, y, w, h])
OcrItem = tuple[str, float, list[float]]

# 進捗ログを出す間隔（ページ数）。数百ページでも冗長すぎず追跡できる粒度。
_PROGRESS_EVERY = 25


def _page_image_reader(im: Image.Image, image_path: str | Path, cfg: Config) -> ImageReader:
    """埋め込む画像を image_format に応じて用意する。

    - jpeg: Pillow で JPEG に再エンコード（jpeg_quality）してから渡す。
      reportlab は JPEG ストリームを DCTDecode でそのまま埋め込むため、
      可逆 PNG(Flate) 埋め込みよりファイルサイズを大きく抑えられる（仕様 P8）。
    - png : 元ファイルをそのまま渡す（可逆・FlateDecode）。
    JPEG は透明度を持てないので RGB に正規化する（テキスト層は画像と独立なので
    検索可能性には影響しない）。
    """
    if cfg.build.image_format.lower() in ("jpeg", "jpg"):
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=cfg.build.jpeg_quality)
        buf.seek(0)
        return ImageReader(buf)
    return ImageReader(str(image_path))


def render_page(c: canvas.Canvas, image_path: str | Path, items: list[OcrItem], cfg: Config) -> None:
    """1ページ分: 画像を敷き、bbox座標に不可視テキスト(RenderMode 3)を重ねる。"""
    dpi = cfg.build.target_dpi
    with Image.open(image_path) as im:
        iw, ih = im.size
        reader = _page_image_reader(im, image_path, cfg)
    pw, ph = iw * 72.0 / dpi, ih * 72.0 / dpi   # ポイント換算
    c.setPageSize((pw, ph))
    c.drawImage(reader, 0, 0, width=pw, height=ph)
    c.setFont(cfg.build.font, 1)
    for text, _conf, (x, y, w, h) in items:
        if not text.strip():
            continue
        c.setFontSize(max(h * ph, 1))       # 箱の高さにフォントを合わせる
        t = c.beginText(x * pw, y * ph)     # 原点左下→そのまま（Y反転不要）
        t.setTextRenderMode(3)              # 3 = 不可視（検索用テキスト）
        t.textLine(text)
        c.drawText(t)
    c.showPage()


def _has_text_layer(items: list[OcrItem]) -> bool:
    """1文字でも可視テキストがあればテキスト層あり（画像のみページの判定用）。"""
    return any(text.strip() for text, _conf, _bbox in items)


def build(pages: list[tuple[str, list[OcrItem]]], out_path: str | Path, cfg: Config) -> None:
    """(image_path, items) のリストから検索可能PDFを1本生成する。

    画像は 1 ページずつ開いて即座に解放し、全ページ同時デコードを避ける
    （数百ページでも安定動作させるため・仕様 P8）。テキスト層が無い（OCR失敗/
    未実施の）ページは画像のみで積み、その枚数をログに記録して継続する。
    """
    # ASCII85 ラッピングを外し JPEG/画像ストリームを二進のまま埋める（約20%削減）。
    # 一括CLIの単発ビルド用途なのでプロセス全体設定でも副作用は問題にならない。
    rl_config.useA85 = 0
    pdfmetrics.registerFont(UnicodeCIDFont(cfg.build.font))  # 日本語CIDフォント
    c = canvas.Canvas(str(out_path))
    total = len(pages)
    logger.info(
        "PDF生成開始: %d ページ（画像形式=%s, JPEG品質=%d, dpi=%d）",
        total, cfg.build.image_format, cfg.build.jpeg_quality, cfg.build.target_dpi,
    )
    image_only = 0
    for i, (image_path, items) in enumerate(pages, start=1):
        if not _has_text_layer(items):
            image_only += 1
        render_page(c, image_path, items, cfg)
        if i % _PROGRESS_EVERY == 0 or i == total:
            logger.info("PDF描画中: %d/%d ページ", i, total)
    c.save()
    if image_only:
        logger.info(
            "テキスト層なし（画像のみ）ページ: %d/%d（OCR失敗/未実施ページを画像のみで継続）",
            image_only, total,
        )
