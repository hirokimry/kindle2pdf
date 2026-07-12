#!/usr/bin/env node
// npx kindle2pdf のエントリ。引数なし + TTY で clack ウィザードを起動し、答えを Python コア
// （python -m kindle2pdf run ...）に渡して撮影する。進捗はコアの JSON Lines を受けてスピナーに
// 描画する。引数ありは passthrough、非TTY は無装飾フォールバックで自動化を壊さない（Issue #34）。
//
// @clack/prompts は wizard 経路でだけ動的 import する。これにより passthrough / fallback や
// 純粋ロジックの単体テストは @clack をインストールせずとも動く。

import { spawn } from "node:child_process";
import { rmSync } from "node:fs";
import { dirname } from "node:path";

import { buildCoreArgs, PAGE_LAYOUTS, validateTitle } from "./lib/args.mjs";
import { ensureConfig } from "./lib/config.mjs";
import { decideMode } from "./lib/mode.mjs";
import { describeEvent } from "./lib/progress.mjs";
import { resolvePython, runCore } from "./lib/runner.mjs";

// コアの出力をそのまま流す（上級者/CI/非TTY）。装飾せず Python CLI と同じ挙動にする。
function passthrough(args) {
  return new Promise((resolve) => {
    const child = spawn(resolvePython(), ["-m", "kindle2pdf", ...args], {
      stdio: "inherit",
    });
    child.on("close", (code) => resolve(code ?? 0));
    child.on("error", () => resolve(1));
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
  let lastOutput = "";
  let errorMessage = "";
  // JSON 化されないコアのエラー（book_title 検証・config 読込失敗などは pipeline.run の
  // 進捗 try の外で起きるため error イベントにならず stderr に出る）も拾って表示する。
  let stderrTail = "";
  let code = 1;
  let configPath = "";
  let generated = false;
  try {
    // config 生成（mkdtemp 失敗等）も含めて try 内に置き、他の失敗経路と同じく
    // 「撮影に失敗しました」で明確に伝える（未捕捉例外でスタックトレース落ちさせない）。
    ({ path: configPath, generated } = ensureConfig());
    const args = buildCoreArgs({ title, layout, configPath, open: true, progress: "json" });
    ({ code } = await runCore({
      args,
      onEvent: (ev) => {
        const msg = describeEvent(ev);
        if (msg) s.message(msg);
        if (ev.event === "complete") lastOutput = ev.output ?? "";
        if (ev.event === "error") errorMessage = ev.message ?? "";
      },
      onStderr: (text) => {
        // 末尾のみ保持（大量ログでメモリを食わない）。失敗時の原因表示に使う。
        stderrTail = (stderrTail + text).slice(-600);
      },
    }));
  } catch (err) {
    // spawn 失敗（python3 不在など）はここに来る。スタックトレースで落とさず明確に伝える。
    errorMessage = err && err.message ? err.message : String(err);
  } finally {
    // 生成した temp config は撮影後に破棄する（cwd は汚さない）。失敗は無視する。
    if (generated) {
      try {
        rmSync(dirname(configPath), { recursive: true, force: true });
      } catch {
        /* 破棄失敗は致命でない（OS の tmp が後で回収する） */
      }
    }
  }

  if (code === 0) {
    s.stop("✅ 完了");
    p.outro(lastOutput ? `📄 ${lastOutput} を開きました` : "完了しました");
    return 0;
  }
  s.stop("❌ 失敗");
  // error イベント > stderr 末尾 の順で最も具体的な原因を見せる。
  const detail = errorMessage || stderrTail.trim();
  p.cancel(detail ? `撮影に失敗しました: ${detail}` : "撮影に失敗しました。ログを確認してください。");
  return code || 1;
}

async function main() {
  const { mode, args } = decideMode(process.argv.slice(2), Boolean(process.stdin.isTTY));
  const code = mode === "wizard" ? await wizard() : await passthrough(args);
  process.exit(code);
}

main();
