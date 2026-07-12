import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";

import { hasIncompleteRun } from "../lib/runs.mjs";

const tmpDirs = [];
function makeCwd() {
  const d = mkdtempSync(join(tmpdir(), "k2p-runs-"));
  tmpDirs.push(d);
  return d;
}
// work/<title>/<run>/state.json を作るヘルパー。
function makeRun(cwd, title, run, stage) {
  const dir = join(cwd, "work", title, run);
  mkdirSync(dir, { recursive: true });
  if (stage !== undefined) {
    writeFileSync(join(dir, "state.json"), JSON.stringify({ stage }), "utf8");
  }
  return dir;
}
after(() => {
  for (const d of tmpDirs) {
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  }
});

test("work/ が無ければ未完了なし", () => {
  assert.equal(hasIncompleteRun("本", makeCwd()), false);
});

test("stage!=done の run があれば未完了あり", () => {
  const cwd = makeCwd();
  makeRun(cwd, "猫", "2026-07-12_100000", "ocr");
  assert.equal(hasIncompleteRun("猫", cwd), true);
});

test("全 run が done なら未完了なし", () => {
  const cwd = makeCwd();
  makeRun(cwd, "猫", "2026-07-12_100000", "done");
  makeRun(cwd, "猫", "2026-07-12_110000", "done");
  assert.equal(hasIncompleteRun("猫", cwd), false);
});

test("done と未完了が混在すれば未完了あり", () => {
  const cwd = makeCwd();
  makeRun(cwd, "猫", "2026-07-12_100000", "done");
  makeRun(cwd, "猫", "2026-07-12_110000", "build");
  assert.equal(hasIncompleteRun("猫", cwd), true);
});

test("state.json 不在の run ディレクトリは未完了扱いにしない（コアと同じ規約）", () => {
  const cwd = makeCwd();
  makeRun(cwd, "猫", "2026-07-12_100000", undefined); // state.json なし
  assert.equal(hasIncompleteRun("猫", cwd), false);
});

test("別タイトルの未完了は影響しない", () => {
  const cwd = makeCwd();
  makeRun(cwd, "犬", "2026-07-12_100000", "ocr");
  assert.equal(hasIncompleteRun("猫", cwd), false);
});
