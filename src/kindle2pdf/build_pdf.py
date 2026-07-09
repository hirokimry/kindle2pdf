"""build 段 — 画像＋透明テキスト層で検索可能PDFを生成する（システムの肝）。

座標変換は **Y反転不要**（Vision・reportlab とも原点左下）。72dpi基準で px=pt 換算。
これ単体で「画像＋不可視テキスト層」の検索可能PDFが完成する（ocrmypdf不要）。

実装チケット: P6(透明テキスト層PDF)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from .config import Config

# ocr.py と同じ (text, confidence, [x, y, w, h])
OcrItem = tuple[str, float, list[float]]


def render_page(c: canvas.Canvas, image_path: str | Path, items: list[OcrItem], dpi: int, font: str) -> None:
    """1ページ分: 画像を敷き、bbox座標に不可視テキスト(RenderMode 3)を重ねる。"""
    with Image.open(image_path) as im:
        iw, ih = im.size
    pw, ph = iw * 72.0 / dpi, ih * 72.0 / dpi   # ポイント換算
    c.setPageSize((pw, ph))
    c.drawImage(ImageReader(str(image_path)), 0, 0, width=pw, height=ph)
    c.setFont(font, 1)
    for text, _conf, (x, y, w, h) in items:
        if not text.strip():
            continue
        c.setFontSize(max(h * ph, 1))       # 箱の高さにフォントを合わせる
        t = c.beginText(x * pw, y * ph)     # 原点左下→そのまま（Y反転不要）
        t.setTextRenderMode(3)              # 3 = 不可視（検索用テキスト）
        t.textLine(text)
        c.drawText(t)
    c.showPage()


def build(pages: list[tuple[str, list[OcrItem]]], out_path: str | Path, cfg: Config) -> None:
    """(image_path, items) のリストから検索可能PDFを1本生成する。"""
    pdfmetrics.registerFont(UnicodeCIDFont(cfg.build.font))  # 日本語CIDフォント
    c = canvas.Canvas(str(out_path))
    for image_path, items in pages:
        render_page(c, image_path, items, cfg.build.target_dpi, cfg.build.font)
    c.save()
