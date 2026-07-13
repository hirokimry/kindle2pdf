#!/usr/bin/env bash
# vibecorp CI（test.yml）が glob する tests/test_*.sh 形式のテスト。
# CI は追加依存を入れないため、calibrate(P1) の構造確認と py_compile のみ行う。
# 挙動の単体テストは tests/test_calibrate.py（pytest, 要 dev 依存）で担保する。
set -euo pipefail

cd "$(dirname "$0")/.."
fail=0

CLI="src/kindle2pdf/cli.py"
CAPTURE="src/kindle2pdf/capture.py"
CONFIG="src/kindle2pdf/config.py"

assert_contains() {
  # $1=ファイル $2=パターン $3=説明
  if grep -q -e "$2" "$1"; then
    echo "OK: $3"
  else
    echo "NG: $3（$1 に '$2' が無い）"
    fail=1
  fi
}

assert_absent() {
  # $1=ファイル $2=パターン $3=説明
  if grep -q -e "$2" "$1"; then
    echo "NG: $3（$1 に '$2' が残っている）"
    fail=1
  else
    echo "OK: $3"
  fi
}

echo "=== calibrate(P1) 構造確認 ==="
assert_absent "$CLI" 'P1: calibrate を実装する' "calibrate が未実装スタブでなくなった"
assert_contains "$CAPTURE" 'def run_calibrate' "capture に run_calibrate が定義された"
assert_contains "$CLI" 'run_calibrate' "cli が run_calibrate を呼ぶ"

echo "=== Python 構文チェック（py_compile） ==="
if command -v python3 >/dev/null 2>&1; then
  python3 -m py_compile "$CLI" "$CAPTURE" "$CONFIG" && echo "OK: 対象モジュールの構文チェック成功"
else
  echo "SKIP: python3 が無い環境"
fi

if [ "$fail" -ne 0 ]; then
  echo "calibrate テスト失敗"
  exit 1
fi
echo "calibrate テスト成功"
