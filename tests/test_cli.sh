#!/usr/bin/env bash
# vibecorp CI（test.yml）が glob する tests/test_*.sh 形式の回帰テスト。
# run コマンドの対話フロント連携（#32: 非対話フラグ・JSON Lines 進捗・自動オープン）を
# 構造チェック + pytest で検証する。pipeline / _open_file / progress シンクは
# monkeypatch で差し替えるため Kindle / osascript / open 実機なしで macOS / Linux(CI) 双方緑。
set -euo pipefail

cd "$(dirname "$0")/.."
fail=0

CLI="src/kindle2pdf/cli.py"
PROGRESS="src/kindle2pdf/progress.py"

assert_contains() {
  # $1=ファイル $2=パターン $3=説明
  if grep -q -e "$2" "$1"; then
    echo "OK: $3"
  else
    echo "NG: $3（$1 に '$2' が無い）"
    fail=1
  fi
}

echo "=== run 対話フロント連携フラグ 構造確認（#32） ==="
# assert_contains は grep -q -e "$2" で照合するため、先頭ダッシュのパターンもそのまま渡せる。
assert_contains "$CLI" '--title' "run に --title が定義された"
assert_contains "$CLI" '--reading-order' "run に --reading-order が定義された"
assert_contains "$CLI" '--no-open' "run に --open/--no-open が定義された"
assert_contains "$CLI" '--progress' "run に --progress が定義された"
assert_contains "$CLI" 'def _open_file' "cli に PDF 自動オープンが定義された"
assert_contains "$PROGRESS" 'def json_lines' "progress に JSON Lines シンクが定義された"

echo "=== Python 構文チェック（py_compile） ==="
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "NG: python が見つからない（CLI テストには Python が必須）"
  exit 1
fi
"$PY" -m py_compile "$CLI" "$PROGRESS" && echo "OK: 対象モジュールの構文チェック成功"

if [ "$fail" -ne 0 ]; then
  echo "cli 構造チェック失敗"
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

echo "=== run 対話フロント連携 回帰テスト（pytest） ==="
"${VENV_DIR}/bin/python" -m pytest tests/test_cli.py tests/test_progress.py -v

echo "cli 対話フロント連携テスト成功"
