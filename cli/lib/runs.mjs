// 選んだ本に未完了の撮影（中断した run）があるかを判定する（Issue #35）。
// 撮影ごとの run ディレクトリは work/<book_title>/<日時>/ で、各 run の state.json の stage が
// "done" でなければ未完了。コア pipeline._incomplete_run_dir と同じ基準（state.json 不在の
// ディレクトリは対象外）にして、フロントとコアで判定を一致させる。#31/#35

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

// title の本に未完了 run があれば true。work/ が無い・全 run 完了なら false。
export function hasIncompleteRun(title, cwd = process.cwd()) {
  const bookDir = join(cwd, "work", title);
  if (!existsSync(bookDir)) return false;
  let entries;
  try {
    entries = readdirSync(bookDir);
  } catch {
    return false;
  }
  for (const name of entries) {
    const runDir = join(bookDir, name);
    try {
      if (!statSync(runDir).isDirectory()) continue;
      const state = JSON.parse(readFileSync(join(runDir, "state.json"), "utf8"));
      if (state && typeof state.stage === "string" && state.stage !== "done") {
        return true;
      }
    } catch {
      // state.json 不在/壊れは未完了扱いにしない（コアと同じ規約）。
      continue;
    }
  }
  return false;
}
