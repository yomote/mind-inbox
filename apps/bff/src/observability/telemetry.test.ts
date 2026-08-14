import { describe, expect, it } from "vitest";
import {
  currentLogger,
  describeError,
  formatTelemetryLine,
  hashIdentifier,
  redactUrl,
  runWithLogger,
  trackDependency,
  trackRequest,
  type TelemetryLogger,
} from "./telemetry";

function recorder(): TelemetryLogger & { lines: string[]; errors: string[] } {
  const lines: string[] = [];
  const errors: string[] = [];
  return {
    lines,
    errors,
    log: (m) => lines.push(m),
    error: (m) => {
      errors.push(m);
      lines.push(m);
    },
  };
}

/** 相談の本文に相当する値。これがテレメトリ行に現れたら事故。 */
const SECRET = "離職を考えていることを誰にも言えていない";

describe("formatTelemetryLine", () => {
  it("許可リストに無いフィールドは値ごと落とす", () => {
    // 無いと何が静かに通るか: ログを 1 行足すだけで相談の本文が
    // Log Analytics に 30 日残る。誰も気づけない。
    const line = formatTelemetryLine("request.start", {
      route: "chat/stream",
      message: SECRET,
      reply: SECRET,
      statement: SECRET,
    });

    expect(line).not.toContain(SECRET);
    expect(line).toContain("route=chat/stream");
  });

  it("落としたフィールドの名前は残す (黙って消さない)", () => {
    // 無いと何が静かに通るか: 「ログに出したつもり」と「落とされた」が
    // 区別できず、テレメトリを見て「その情報は無い」と誤読する。
    const line = formatTelemetryLine("request.start", { reply: SECRET, message: SECRET });

    expect(line).toContain("dropped=message,reply");
  });

  it("url は呼び出し側が忘れてもクエリを落とす", () => {
    // 無いと何が静かに通るか: 下流 URL のクエリに本文やトークンを付けた瞬間、
    // それがそのままテレメトリに焼かれる。
    const line = formatTelemetryLine("dependency.start", {
      target: "ai-agent",
      url: `https://ai.example.com/chat?q=${encodeURIComponent(SECRET)}&token=abc`,
    });

    expect(line).toContain("url=https://ai.example.com/chat");
    expect(line).not.toContain("token=abc");
    expect(line).not.toContain(encodeURIComponent(SECRET));
  });

  it("errorMessage は 1 行に潰して上限で切る", () => {
    // 無いと何が静かに通るか: 例外文面に混ざった応答本文が丸ごと乗り、
    // 改行で 1 イベントが複数行に割れて突き合わせも壊れる。
    const line = formatTelemetryLine("dependency.end", {
      errorType: "Error",
      errorMessage: `boom\n${"x".repeat(500)}`,
    });

    expect(line.split("\n")).toHaveLength(1);
    expect(line).not.toContain("x".repeat(301));
  });

  it("値に空白があっても 1 フィールドとして読める形にする", () => {
    const line = formatTelemetryLine("dependency.end", { operation: "POST /chat stream" });
    expect(line).toContain('operation="POST /chat stream"');
  });

  it("null と undefined を区別する (undefined は出さない)", () => {
    const line = formatTelemetryLine("x", { sessionId: null, status: undefined });
    expect(line).toBe("event=x sessionId=null");
  });
});

describe("redactUrl", () => {
  it("読めない URL を推測で通さない", () => {
    // 壊れた URL にこそ本文が混ざりうるので、元文字列は出さない。
    expect(redactUrl(`not a url ${SECRET}`)).toBe("(unparsable-url)");
  });
});

describe("hashIdentifier", () => {
  it("同じ入力で同じ値になり、元の値は含まない", () => {
    const userId = "user-0e2f-abc";
    expect(hashIdentifier(userId)).toBe(hashIdentifier(userId));
    expect(hashIdentifier(userId)).not.toContain(userId);
  });
});

describe("runWithLogger", () => {
  it("await をまたいでも invocation のロガーを返す", async () => {
    // 無いと何が静かに通るか: 下流クライアントのログが console 送りになり、
    // どの invocation の話か紐づかない (並行リクエストで突き合わせ不能)。
    const log = recorder();

    await runWithLogger(log, async () => {
      await new Promise((resolve) => setTimeout(resolve, 1));
      currentLogger().log("after-await");
    });

    expect(log.lines).toEqual(["after-await"]);
  });
});

describe("trackDependency", () => {
  it("成功時に開始と終了 (所要 ms つき) を対で残す", async () => {
    const log = recorder();

    const value = await runWithLogger(log, () =>
      trackDependency({ target: "ai-agent", operation: "POST /chat" }, async () => "ok"),
    );

    expect(value).toBe("ok");
    expect(log.lines[0]).toContain("event=dependency.start target=ai-agent");
    expect(log.lines[1]).toContain("event=dependency.end");
    expect(log.lines[1]).toContain("outcome=success");
    expect(log.lines[1]).toMatch(/ms=\d+/);
  });

  it("失敗時も開始行を残し、終了行を outcome=failure で出して例外は素通しする", async () => {
    // 無いと何が静かに通るか: 「呼んだが返ってこなかった」(開始だけ) と
    // 「呼んですぐ失敗した」(開始 + failure) が区別できない。#293 の本丸。
    const log = recorder();

    await expect(
      runWithLogger(log, () =>
        trackDependency({ target: "voicevox", operation: "POST /synthesize" }, async () => {
          throw new Error("upstream down");
        }),
      ),
    ).rejects.toThrow("upstream down");

    expect(log.lines[0]).toContain("event=dependency.start target=voicevox");
    expect(log.errors[0]).toContain("outcome=failure");
    expect(log.errors[0]).toContain("errorType=Error");
  });
});

describe("trackRequest", () => {
  it("SSE は completed ではなく stream-opened として記録する", async () => {
    // 無いと何が静かに通るか: ストリームを**開いた**だけで「完了」と記録され、
    // 「最後まで流れたか」に答えられないのに答えたつもりになる。
    const log = recorder();

    await trackRequest(
      "chat/stream",
      log,
      async () =>
        new Response("data: {}\n\n", { headers: { "Content-Type": "text/event-stream" } }),
    );

    expect(log.lines[1]).toContain("outcome=stream-opened");
  });

  it("通常応答は status つきで completed", async () => {
    const log = recorder();

    await trackRequest("warmup", log, async () => new Response(null, { status: 204 }));

    expect(log.lines[1]).toContain("status=204");
    expect(log.lines[1]).toContain("outcome=completed");
  });

  it("ハンドラが投げた例外を記録してから再送出する", async () => {
    const log = recorder();

    await expect(
      trackRequest("trpc", log, async () => {
        throw new Error("boom");
      }),
    ).rejects.toThrow("boom");

    expect(log.errors[0]).toContain("outcome=threw");
  });

  it("ハンドラの中から currentLogger() で同じロガーが引ける", async () => {
    const log = recorder();

    await trackRequest("tts", log, async () => {
      currentLogger().log("inside");
      return new Response(null, { status: 204 });
    });

    expect(log.lines).toContain("inside");
  });
});

describe("describeError", () => {
  it("Error 以外も種別を落とさない", () => {
    expect(describeError("plain string")).toEqual({
      errorType: "string",
      errorMessage: "plain string",
    });
  });
});
