// 起動モードを決める純粋ロジック（Issue #34）。
// - 引数あり: passthrough（上級者/CI が `npx kindle2pdf run --config ...` を直叩き）
// - 引数なし + TTY: wizard（対話ウィザード）
// - 引数なし + 非TTY: fallback（無装飾で `run --config config.yaml`。自動化を壊さない）
export function decideMode(argv, isTTY) {
  const args = (argv ?? []).filter((a) => a !== "");
  if (args.length > 0) return { mode: "passthrough", args };
  if (isTTY) return { mode: "wizard", args: [] };
  return { mode: "fallback", args: ["run", "--config", "config.yaml"] };
}
