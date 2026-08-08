/**
 * SSE (text/event-stream) の data 行を JSON として逐次取り出す最小パーサ (#120)。
 *
 * fetch の ReadableStream から読み、`\n\n` 区切りのイベント毎に `data:` 行を
 * 連結して JSON.parse する。壊れたイベントは黙って捨てる (ストリーム全体を
 * 落とすより、done 欠落として呼び出し側のフォールバックに任せる方が安全)。
 */
export async function* parseSseJsonStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const drainEvents = function* () {
    let idx = buffer.indexOf("\n\n");
    while (idx !== -1) {
      const rawEvent = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);

      const data = rawEvent
        .split("\n")
        .map((line) => line.replace(/\r$/, ""))
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice("data:".length).replace(/^ /, ""))
        .join("\n");

      if (data) {
        try {
          yield JSON.parse(data) as unknown;
        } catch {
          // 壊れたイベントは捨てる
        }
      }
      idx = buffer.indexOf("\n\n");
    }
  };

  // CRLF は LF に正規化してから走査する (末尾に孤立した \r が残った場合は
  // 次チャンクで \r\n が完成してから置換されるので取りこぼさない)。
  const normalize = () => {
    buffer = buffer.replace(/\r\n/g, "\n");
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      normalize();
      yield* drainEvents();
    }
    buffer += decoder.decode();
    normalize();
    yield* drainEvents();
  } finally {
    reader.releaseLock();
  }
}
