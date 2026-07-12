#!/usr/bin/env bash
# vibecorp CI（test.yml）が glob する tests/test_*.sh 形式のテスト。
# Node 製フロント（npx kindle2pdf ウィザード・Issue #34）の純粋ロジックを node --test で検証する。
# テストは @clack を import しない lib/ のみを対象にし、spawn はフェイクで差し替えるため
# npm install / python / Kindle 実機なしでオフライン実行できる（macOS / Linux CI 双方で緑）。
set -euo pipefail

cd "$(dirname "$0")/.."
fail=0

assert_contains() {
  # $1=ファイル $2=パターン $3=説明
  if grep -q -e "$2" "$1"; then
    echo "OK: ${3}"
  else
    # 変数展開の直後に全角文字が続く場合はブレースで囲む（macOS bash 3.2 の
    # UTF-8 マルチバイト unbound variable バグ回避・shell.md 準拠）。
    echo "NG: ${3}（${1} に '${2}' が無い）"
    fail=1
  fi
}

echo "=== Node フロント 構造確認（#34） ==="
assert_contains "package.json" '"kindle2pdf": "cli/index.mjs"' "package.json の bin が cli/index.mjs を指す"
assert_contains "package.json" '@clack/prompts' "依存に @clack/prompts がある"
assert_contains "cli/index.mjs" 'python' "フロントが Python コアを起動する（-m kindle2pdf）"
assert_contains "cli/lib/runner.mjs" '"-m", "kindle2pdf"' "コアをモジュール形式で呼ぶ（bin 名衝突回避）"

if [ "$fail" -ne 0 ]; then
  echo "Node フロント構造チェック失敗"
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "NG: node が見つからない（Node フロントのテストには Node が必須）"
  exit 1
fi

echo "=== Node フロント 単体テスト（node --test） ==="
node --test "cli/test/*.test.mjs"

echo "Node フロントテスト成功"
