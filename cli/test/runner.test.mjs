import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { test } from "node:test";

import { resolvePython, runCore } from "../lib/runner.mjs";

// spawn を差し替えるための最小のフェイク子プロセス。stdout/stderr は EventEmitter。
function fakeSpawn(script) {
  const captured = {};
  const impl = (cmd, argv) => {
    captured.cmd = cmd;
    captured.argv = argv;
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    setImmediate(() => script(child));
    return child;
  };
  return { impl, captured };
}

test("resolvePython: 環境変数 KINDLE2PDF_PYTHON を優先、既定は python3", () => {
  const saved = process.env.KINDLE2PDF_PYTHON;
  delete process.env.KINDLE2PDF_PYTHON;
  assert.equal(resolvePython(), "python3");
  process.env.KINDLE2PDF_PYTHON = "/venv/bin/python";
  assert.equal(resolvePython(), "/venv/bin/python");
  if (saved === undefined) delete process.env.KINDLE2PDF_PYTHON;
  else process.env.KINDLE2PDF_PYTHON = saved;
});

test("runCore: -m kindle2pdf を前置してコアを起動し、JSON Lines を onEvent に流す", async () => {
  const { impl, captured } = fakeSpawn((child) => {
    // 複数行 + stderr + 末尾改行なしの最終行を送る。
    child.stdout.emit(
      "data",
      '{"event":"stage_start","stage":"capture"}\n' +
        '{"event":"page","stage":"capture","page":1,"total":null}\n',
    );
    child.stderr.emit("data", "INFO 人間向けログ\n");
    child.stdout.emit("data", '{"event":"complete","output":"work/x/output/x.pdf"}');
    child.emit("close", 0);
  });

  const events = [];
  const errs = [];
  const { code } = await runCore({
    pythonCmd: "python3",
    args: ["run", "--title", "猫", "--progress", "json"],
    onEvent: (e) => events.push(e),
    onStderr: (s) => errs.push(s),
    spawnImpl: impl,
  });

  assert.equal(code, 0);
  assert.deepEqual(captured.argv, [
    "-m",
    "kindle2pdf",
    "run",
    "--title",
    "猫",
    "--progress",
    "json",
  ]);
  assert.deepEqual(events.map((e) => e.event), ["stage_start", "page", "complete"]);
  assert.equal(events[2].output, "work/x/output/x.pdf"); // 末尾改行なしの行も拾う
  assert.ok(errs.join("").includes("人間向けログ"));
});

test("runCore: 非ゼロ終了は reject せず code を返す", async () => {
  const { impl } = fakeSpawn((child) => {
    child.stdout.emit("data", '{"event":"error","stage":"capture","message":"未起動"}\n');
    child.emit("close", 1);
  });
  const events = [];
  const { code } = await runCore({ args: ["run"], onEvent: (e) => events.push(e), spawnImpl: impl });
  assert.equal(code, 1);
  assert.equal(events[0].event, "error");
});

test("runCore: spawn 自体のエラーは reject する", async () => {
  const impl = () => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    setImmediate(() => child.emit("error", new Error("ENOENT python3")));
    return child;
  };
  await assert.rejects(() => runCore({ args: ["run"], spawnImpl: impl }), /ENOENT/);
});
