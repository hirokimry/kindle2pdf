// コアが標準出力に流す JSON Lines 進捗（Issue #32）を1行ずつパースし、
// スピナー／進捗バーに描画するための人間向けラベルへ変換する。純粋ロジック（Issue #34）。

const STAGE_LABELS = {
  capture: "撮影",
  preprocess: "前処理",
  ocr: "OCR",
  build: "PDF生成",
};

// 進捗1行を JSON としてパースする。空行・JSON でない行（人間向けログ混在）は null を返す。
export function parseProgressLine(line) {
  const trimmed = (line ?? "").trim();
  if (!trimmed || trimmed[0] !== "{") return null;
  try {
    const obj = JSON.parse(trimmed);
    return obj && typeof obj.event === "string" ? obj : null;
  } catch {
    return null;
  }
}

// パース済みイベントをスピナー表示用の1行メッセージに変換する。
export function describeEvent(ev) {
  if (!ev || typeof ev.event !== "string") return null;
  const stage = STAGE_LABELS[ev.stage] ?? ev.stage;
  switch (ev.event) {
    case "stage_start":
      return `${stage} を開始`;
    case "stage_complete":
      return `${stage} が完了`;
    case "page": {
      if (ev.total != null) return `${stage}: ${ev.page}/${ev.total} ページ`;
      return `${stage}: ${ev.page} ページ`;
    }
    case "complete":
      return `完了: ${ev.output ?? ""}`;
    case "error":
      return `エラー（${stage}）: ${ev.message ?? ""}`;
    default:
      return null;
  }
}
