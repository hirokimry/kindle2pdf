import assert from "node:assert/strict";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { runCaptureWithProgress } from "../lib/capture.mjs";

// spinner のフェイク（message を記録するだけ）。
function fakeSpinner() {
  const messages = [];
  return { messages, message: (m) => messages.push(m) };
}

// ensureConfig のフェイク。generated=true の temp を返し、破棄されたか確認できるようにする。
function fakeGeneratedConfig() {
  const dir = mkdtempSync(join(tmpdir(), "k2p-cap-"));
  const path = join(dir, "config.yaml");
  writeFileSync(path, "capture:\n  auto_region: true\n", "utf8");
  return { path, generated: true, dir };
}

test("runCaptureWithProgress: 進捗を spinner に描画し complete で output を返す", async () => {
  const spinner = fakeSpinner();
  const cfg = fakeGeneratedConfig();
  const run = async ({ onEvent }) => {
    onEvent({ event: "stage_start", stage: "capture" });
    onEvent({ event: "page", stage: "ocr", page: 1, total: 2 });
    onEvent({ event: "complete", output: "work/x/output/x.pdf" });
    return { code: 0 };
  };

  const res = await runCaptureWithProgress(
    { title: "猫", layout: "single" },
    { spinner, ensure: () => cfg, run },
  );

  assert.equal(res.code, 0);
  assert.equal(res.output, "work/x/output/x.pdf");
  assert.equal(res.error, "");
  assert.ok(spinner.messages.includes("撮影 を開始"));
  assert.ok(spinner.messages.includes("OCR: 1/2 ページ"));
  assert.equal(existsSync(cfg.dir), false); // 生成した temp config は破棄される
});

test("runCaptureWithProgress: resume=false をコア引数（--no-resume）まで届ける", async () => {
  const cfg = fakeGeneratedConfig();
  let receivedArgs;
  const run = async ({ args, onEvent }) => {
    receivedArgs = args;
    onEvent({ event: "complete", output: "x.pdf" });
    return { code: 0 };
  };

  await runCaptureWithProgress(
    { title: "猫", layout: "single", resume: false },
    { spinner: fakeSpinner(), ensure: () => cfg, run },
  );

  assert.ok(receivedArgs.includes("--no-resume"));
});

test("runCaptureWithProgress: error イベントを error に集約する", async () => {
  const cfg = fakeGeneratedConfig();
  const run = async ({ onEvent }) => {
    onEvent({ event: "error", stage: "capture", message: "Kindle未起動" });
    return { code: 1 };
  };

  const res = await runCaptureWithProgress(
    { title: "本", layout: "single" },
    { spinner: fakeSpinner(), ensure: () => cfg, run },
  );

  assert.equal(res.code, 1);
  assert.match(res.error, /Kindle未起動/);
});

test("runCaptureWithProgress: JSON化されない失敗は stderr 末尾を error にする", async () => {
  const cfg = fakeGeneratedConfig();
  const run = async ({ onStderr }) => {
    onStderr("Error: book_title にパス区切りは使えません\n");
    return { code: 1 };
  };

  const res = await runCaptureWithProgress(
    { title: "本", layout: "single" },
    { spinner: fakeSpinner(), ensure: () => cfg, run },
  );

  assert.equal(res.code, 1);
  assert.match(res.error, /book_title/);
});

test("runCaptureWithProgress: run が throw（spawn 失敗）しても落ちず error を返す", async () => {
  const cfg = fakeGeneratedConfig();
  const run = async () => {
    throw new Error("ENOENT python3");
  };

  const res = await runCaptureWithProgress(
    { title: "本", layout: "single" },
    { spinner: fakeSpinner(), ensure: () => cfg, run },
  );

  assert.equal(res.code, 1);
  assert.match(res.error, /ENOENT/);
  assert.equal(existsSync(cfg.dir), false); // 失敗時も temp を破棄する
});

test("runCaptureWithProgress: ensure が throw（config生成失敗）しても落ちず error を返す", async () => {
  const res = await runCaptureWithProgress(
    { title: "本", layout: "single" },
    {
      spinner: fakeSpinner(),
      ensure: () => {
        throw new Error("EROFS read-only tmp");
      },
      run: async () => ({ code: 0 }),
    },
  );

  assert.equal(res.code, 1);
  assert.match(res.error, /EROFS/);
});
