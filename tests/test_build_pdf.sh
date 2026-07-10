#!/usr/bin/env bash
# vibecorp CI（test.yml）が glob する tests/test_*.sh 形式のゴールデン回帰テスト。
# build_pdf の透明テキスト層PDFを実際に生成し、pytest で「検索ヒット・座標変換の
# 妥当性・Y反転なし・px→pt換算固定」を検証する。著作物(PoC実画像)に依存しない
# 自己完結型なので macOS / Linux(CI) の双方で緑になる。
set -euo pipefail

cd "$(dirname "$0")/.."

# 本テストは PDF 生成と抽出を実際に行うため python を必須とする
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "NG: python が見つからない（build_pdf ゴールデン回帰には Python が必須）"
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

echo "=== build_pdf ゴールデン回帰テスト（pytest） ==="
"${VENV_DIR}/bin/python" -m pytest tests/test_build_pdf.py -v

echo "build_pdf ゴールデン回帰テスト成功"
