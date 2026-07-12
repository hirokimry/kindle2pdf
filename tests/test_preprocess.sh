#!/usr/bin/env bash
# P4(トリミング・1 撮影 = 1 ページ)の検証。
# vibecorp CI（test.yml）が glob する tests/test_*.sh 形式。
#
# process_all の中核ロジック（1 撮影 = 1 ページ・トリミング・黒画面除外・冪等クリア・
# レジューム）を pytest で実際に実行して検証する。見開きの左右分割は廃止した（Issue #29）。
# 合成画像で自己完結するため著作物に依存せず macOS / Linux(CI) の双方で緑になる。
set -euo pipefail

cd "$(dirname "$0")/.."

# 本テストは実際に画像をトリミング・書き出して検証するため python を必須とする
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "NG: python が見つからない（preprocess 挙動検証には Python が必須）"
  exit 1
fi

# 独立した一時 venv を作り、リポジトリを editable インストールして pytest を回す
VENV_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${VENV_DIR}" || true
}
trap cleanup EXIT

echo "=== venv 作成: ${VENV_DIR} ==="
"${PY}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install -q --upgrade pip
"${VENV_DIR}/bin/pip" install -q -e ".[dev]"

echo "=== preprocess 挙動検証（pytest） ==="
"${VENV_DIR}/bin/python" -m pytest tests/test_preprocess.py -v

echo "preprocess 挙動検証成功"
