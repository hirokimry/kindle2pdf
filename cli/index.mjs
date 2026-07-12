#!/usr/bin/env node
// npx kindle2pdf のエントリ。引数なし + TTY で clack ウィザードを起動し、答えを Python コア
// （python -m kindle2pdf run ...）に渡して撮影する。進捗はコアの JSON Lines を受けてスピナーに
// 描画する。引数ありは passthrough、非TTY は無装飾フォールバックで自動化を壊さない（Issue #34）。
//
// @clack/prompts は wizard 経路でだけ動的 import する。これにより passthrough / fallback や
// 純粋ロジックの単体テストは @clack をインストールせずとも動く。

import { spawn } from "node:child_process";

import { PAGE_LAYOUTS, validateTitle } from "./lib/args.mjs";
import { runCaptureWithProgress } from "./lib/capture.mjs";
import { decideMode } from "./lib/mode.mjs";
import { resolvePython } from "./lib/runner.mjs";

// コアの出力をそのまま流す（上級者/CI/非TTY）。装飾せず Python CLI と同じ挙動にする。
function passthrough(args) {
  return new Promise((resolve) => {
    const py = resolvePython();
    const child = spawn(py, ["-m", "kindle2pdf", ...args], { stdio: "inherit" });
    // シグナル終了（code=null, signal="SIGKILL" 等）を成功扱いにしない（runner と同じ規約）。
    child.on("close", (code, signal) => resolve(code ?? (signal ? 1 : 0)));
    child.on("error", (err) => {
      // spawn 失敗（python3 不在等）は stdio:inherit でも何も出ないので明示して伝える。
      process.stderr.write(`kindle2pdf: Python コアを起動できません（${py}）: ${err.message}\n`);
      resolve(1);
    });
  });
}

async function wizard() {
  const p = await import("@clack/prompts");
  p.intro("📚 kindle2pdf — Kindle本を検索可能PDFに");

  const title = await p.text({
    message: "📖 本のタイトルは？",
    placeholder: "例: 吾輩は猫である",
    // book_title は work/<book_title>/ のフォルダ名になる。コア Config.validate() と同じ
    // 規則（区切り / 相対参照 / 空文字を弾く）で早めに気づけるようにする（#32/#34）。
    validate: validateTitle,
  });
  if (p.isCancel(title)) {
    p.cancel("中止しました");
    return 1;
  }

  const layout = await p.select({
    message: "📐 ページ構成は？（Kindle ウィンドウ幅で選びます）",
    options: PAGE_LAYOUTS.map((l) => ({ value: l.value, label: l.label, hint: l.hint })),
  });
  if (p.isCancel(layout)) {
    p.cancel("中止しました");
    return 1;
  }

  const s = p.spinner();
  s.start("撮影を準備中…");
  // config 生成・撮影・進捗描画・temp 破棄・エラー集約は runCaptureWithProgress に委ねる
  // （@clack 非依存でテスト可能。wizard() は clack の入出力だけを担う）。
  const { code, output, error } = await runCaptureWithProgress({ title, layout }, { spinner: s });

  if (code === 0) {
    s.stop("✅ 完了");
    p.outro(output ? `📄 ${output} を開きました` : "完了しました");
    return 0;
  }
  s.stop("❌ 失敗");
  p.cancel(error ? `撮影に失敗しました: ${error}` : "撮影に失敗しました。ログを確認してください。");
  return code || 1;
}

async function main() {
  const { mode, args } = decideMode(process.argv.slice(2), Boolean(process.stdin.isTTY));
  const code = mode === "wizard" ? await wizard() : await passthrough(args);
  process.exit(code);
}

main();
