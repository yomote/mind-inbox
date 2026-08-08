/**
 * [L1] SSE パーサの単体テスト。
 *
 * 無いと何が静かに通るか: チャンク境界がイベント途中で切れるケース (実ネットワークでは
 * 常に起きる) の取りこぼしや、壊れた 1 イベントでストリーム全体を落とす退行が、
 * happy path だけの L2 では検知されず本番で「たまに途中で止まる」として出る。
 */

import { describe, expect, it } from "vitest";
import { parseSseJsonStream } from "./sse";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<unknown[]> {
  const out: unknown[] = [];
  for await (const event of parseSseJsonStream(stream)) out.push(event);
  return out;
}

describe("[L1] parseSseJsonStream", () => {
  it("data 行の JSON を順に取り出す", async () => {
    const events = await collect(
      streamOf(['data: {"type":"delta","text":"a"}\n\ndata: {"type":"done"}\n\n']),
    );
    expect(events).toEqual([{ type: "delta", text: "a" }, { type: "done" }]);
  });

  it("イベント途中でチャンクが切れても正しく組み立てる", async () => {
    const events = await collect(
      streamOf(['data: {"type":"del', 'ta","text":"あい"}', "\n", '\ndata: {"type":"done"}\n\n']),
    );
    expect(events).toEqual([{ type: "delta", text: "あい" }, { type: "done" }]);
  });

  it("CRLF 改行のサーバでも読める", async () => {
    const events = await collect(streamOf(['data: {"type":"done"}\r\n\r\n']));
    // \r\n\r\n は \n\n を含まない形になるため、行末 \r を落として解釈できること
    expect(events).toEqual([{ type: "done" }]);
  });

  it("壊れた JSON のイベントは捨てて後続を処理する", async () => {
    const events = await collect(streamOf(["data: {broken\n\n", 'data: {"type":"done"}\n\n']));
    expect(events).toEqual([{ type: "done" }]);
  });
});
