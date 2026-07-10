"""ページ連番ファイル名の一元採番。

capture(raw/) と preprocess(pages/) が同じゼロ埋め桁でページ画像を採番するための
単一の定義点。桁を十分広く取り、config の max_pages（既定3000・見開き分割で最大2倍）や
将来拡張でも `sorted()` の辞書順とページ番号順が一致し続けるようにする（P8: 数百ページ安定動作）。

実装チケット: P8(仕上げ・ゼロ埋め桁統一)
"""

from __future__ import annotations

# ページ連番のゼロ埋め桁数。6桁=最大999,999ページまで辞書順ソートが破綻しない。
# 実在するどの書籍のページ数（分割後）も十分に上回る余裕を持たせる。
PAGE_NUM_WIDTH = 6


def page_filename(n: int) -> str:
    """ページ番号 n から `page_000000.png` 形式のファイル名を返す。"""
    return f"page_{n:0{PAGE_NUM_WIDTH}d}.png"
