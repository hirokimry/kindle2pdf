import assert from "node:assert/strict";
import { test } from "node:test";

import { decideMode } from "../lib/mode.mjs";

test("引数ありは passthrough（上級者/CI 直叩き）", () => {
  assert.deepEqual(decideMode(["run", "--config", "config.yaml"], true), {
    mode: "passthrough",
    args: ["run", "--config", "config.yaml"],
  });
  // TTY でなくても引数ありなら passthrough
  assert.equal(decideMode(["--help"], false).mode, "passthrough");
});

test("引数なし + TTY はウィザード", () => {
  assert.deepEqual(decideMode([], true), { mode: "wizard", args: [] });
});

test("引数なし + 非TTY は無装飾フォールバック（run --config config.yaml）", () => {
  assert.deepEqual(decideMode([], false), {
    mode: "fallback",
    args: ["run", "--config", "config.yaml"],
  });
});

test("空文字引数は無視する", () => {
  assert.equal(decideMode(["", ""], true).mode, "wizard");
});
