// Python コア（python -m kindle2pdf ...）を子プロセスで起動し、標準出力の JSON Lines 進捗を
// 1 行ずつ onEvent に流す。console_script `kindle2pdf` は Node の bin 名と衝突するため、
// 必ずモジュール形式（-m kindle2pdf）で呼ぶ（Issue #34）。spawn は差し替え可能でテストする。

import { spawn as nodeSpawn } from "node:child_process";

import { parseProgressLine } from "./progress.mjs";

// 使う Python を決める。環境変数で明示できる（venv の python 等）。既定は python3。
export function resolvePython() {
  return process.env.KINDLE2PDF_PYTHON || "python3";
}

// コアを起動し、進捗イベントを onEvent(ev) に、生ログ（stderr）を onStderr(text) に渡す。
// Promise は終了コードで解決する（失敗でも reject せず code を返し、呼び出し側が判断する）。
export function runCore({
  pythonCmd = resolvePython(),
  args = [],
  onEvent = () => {},
  onStderr = () => {},
  spawnImpl = nodeSpawn,
} = {}) {
  return new Promise((resolve, reject) => {
    const child = spawnImpl(pythonCmd, ["-m", "kindle2pdf", ...args]);
    let buf = "";

    child.stdout.setEncoding?.("utf8");
    child.stdout.on("data", (chunk) => {
      buf += chunk;
      let nl;
      // 行が揃うたびにパースして流す。半端な行末はバッファに残す。
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        const ev = parseProgressLine(line);
        if (ev) onEvent(ev);
      }
    });

    child.stderr?.setEncoding?.("utf8");
    child.stderr?.on("data", (chunk) => onStderr(String(chunk)));

    child.on("error", (err) => reject(err));
    child.on("close", (code) => {
      // 末尾に改行なしで残った最終行も処理する。
      const ev = parseProgressLine(buf);
      if (ev) onEvent(ev);
      resolve({ code: code ?? 0 });
    });
  });
}
