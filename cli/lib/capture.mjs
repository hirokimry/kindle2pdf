// ウィザードの中核ロジック: 回答から撮影を実行し、進捗を spinner に描画する。
// ensureConfig（temp config 生成）→ buildCoreArgs → runCore → temp 破棄 → エラー集約を
// まとめる。@clack に依存しないため node --test で単体検証できる（Issue #34）。
// wizard() 側は clack の入出力だけを担い、実処理は本関数に委ねる（責務分離・テスタビリティ）。

import { rmSync } from "node:fs";
import { dirname } from "node:path";

import { buildCoreArgs } from "./args.mjs";
import { ensureConfig } from "./config.mjs";
import { describeEvent } from "./progress.mjs";
import { runCore } from "./runner.mjs";

// spinner は { message(text) } を持つ最小インターフェース（clack の spinner 互換）。
// ensure / run はテスト注入用の差し替え口（既定は実物）。
// 返り値 { code, output, error }。error は「error イベント > stderr 末尾」の順で最も具体的な原因。
export async function runCaptureWithProgress(
  { title, layout },
  { spinner, ensure = ensureConfig, run = runCore } = {},
) {
  let lastOutput = "";
  let errorMessage = "";
  // JSON 化されないコアのエラー（book_title 検証・config 読込失敗などは pipeline.run の
  // 進捗 try の外で起きるため error イベントにならず stderr に出る）も拾って返す。
  let stderrTail = "";
  let code = 1;
  let configPath = "";
  let generated = false;
  try {
    ({ path: configPath, generated } = ensure());
    const args = buildCoreArgs({ title, layout, configPath, open: true, progress: "json" });
    ({ code } = await run({
      args,
      onEvent: (ev) => {
        const msg = describeEvent(ev);
        if (msg && spinner) spinner.message(msg);
        if (ev.event === "complete") lastOutput = ev.output ?? "";
        if (ev.event === "error") errorMessage = ev.message ?? "";
      },
      onStderr: (text) => {
        stderrTail = (stderrTail + text).slice(-600);
      },
    }));
  } catch (err) {
    // spawn 失敗（python3 不在など）や config 生成失敗はここに来る。呼び出し側で明確に伝える。
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
  return { code, output: lastOutput, error: errorMessage || stderrTail.trim() };
}
