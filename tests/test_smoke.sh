#!/usr/bin/env bash
# vibecorp CI（test.yml）が glob する tests/test_*.sh 形式のスモークテスト。
# macOS依存なし・追加依存なしで動くよう、構造確認と py_compile のみ行う。
set -euo pipefail

cd "$(dirname "$0")/.."
fail=0

require() {
  if [ ! -e "$1" ]; then
    echo "NG: 必須ファイルが無い: $1"
    fail=1
  fi
}

echo "=== 構造確認 ==="
require pyproject.toml
require config.example.yaml
require README.md
require src/kindle2pdf/__init__.py
require src/kindle2pdf/cli.py
require src/kindle2pdf/config.py
require src/kindle2pdf/state.py
require src/kindle2pdf/imaging.py
require src/kindle2pdf/capture.py
require src/kindle2pdf/preprocess.py
require src/kindle2pdf/ocr.py
require src/kindle2pdf/build_pdf.py
require src/kindle2pdf/pipeline.py
require docs/kindle2pdf/01_spec-design.md

echo "=== Python 構文チェック（py_compile） ==="
if command -v python3 >/dev/null 2>&1; then
  python3 -m py_compile src/kindle2pdf/*.py && echo "OK: 全モジュールの構文チェック成功"
else
  echo "SKIP: python3 が無い環境"
fi

if [ "$fail" -ne 0 ]; then
  echo "スモークテスト失敗"
  exit 1
fi
echo "スモークテスト成功"
