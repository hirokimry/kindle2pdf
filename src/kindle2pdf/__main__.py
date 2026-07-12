"""`python -m kindle2pdf ...` エントリポイント。

Node 製フロント（npx kindle2pdf）はコアをモジュール形式で起動する。console_script
`kindle2pdf` は Node の bin 名と衝突し PATH 解決が曖昧になるため、フロントからは
`python3 -m kindle2pdf` で確実にコアを呼ぶ（Issue #34）。
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
