#!/usr/bin/env bash
# vibecorp CI（test.yml）が glob する tests/test_*.sh 形式の回帰テスト。
# pipeline の4段結線（capture→preprocess→ocr→build）とレジューム（F-8）を pytest で
# 検証する。capture（Kindle操作）と ocr_page（Apple Vision, macOS専用）だけを
# monkeypatch で差し替え、preprocess / build は実物で通すため macOS / Linux(CI) 双方で緑になる。
set -euo pipefail

cd "$(dirname "$0")/.."

# 本テストは PDF 生成と抽出を実際に行うため python を必須とする
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "NG: python が見つからない（pipeline 統合テストには Python が必須）"
  exit 1
fi

# 独立した一時 venv を作り、リポジトリを editable インストールして pytest を回す
VENV_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$VENV_DIR" || true
}
trap cleanup EXIT

echo "=== venv 作成: ${VENV_DIR} ==="
"$PY" -m venv "$VENV_DIR"
"${VENV_DIR}/bin/python" -m pip install -q --upgrade pip
"${VENV_DIR}/bin/pip" install -q -e ".[dev]"

echo "=== pipeline 統合＋レジューム回帰テスト（pytest） ==="
"${VENV_DIR}/bin/python" -m pytest tests/test_pipeline.py -v

echo "pipeline 統合＋レジューム回帰テスト成功"
