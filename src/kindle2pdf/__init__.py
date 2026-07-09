"""kindle2pdf — Kindle本を検索可能PDF化するフル自動パイプライン。

段構成:
    capture   … Kindle制御・撮影・最終ページ検出（唯一Kindleに触れる段）
    preprocess … 見開き分割・トリミング・正規化（バッチ）
    ocr        … Apple Vision OCR（バッチ）
    build      … 画像＋透明テキスト層で検索可能PDF生成（バッチ）
"""

__version__ = "0.1.0"
