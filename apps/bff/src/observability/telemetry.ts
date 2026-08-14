/**
 * BFF のテレメトリ (構造化ログ) の**唯一の出口** — #307。
 *
 * ## 何のためにあるか
 *
 * 実環境で「BFF が下流を呼んだのか / 何 ms で何が返ったのか」に後から答えるため。
 * #293 では丸一日「SSE がハングしている」という誤った仮説で動いたが、**サーバ側に
 * 記録が無かったので「そもそもリクエストが来ていない」を確定できなかった**。
 * ここが埋まると、少なくとも「犯人はサーバに居ない」を数分で言えるようになる。
 *
 * ## この層が単独で守る不変条件
 *
 * > **相談の本文をテレメトリに載せない。**
 *
 * Application Insights / Log Analytics に入った行は 30 日残り、Azure のロールを
 * 持つ人なら誰でも読める。相談の本文 (message / reply / statement / excerpt /
 * title / summary / concern) はこのプロダクトで最も機微なデータで、Cosmos の
 * アクセス制御 (ADR 0030) を回り込んで**平文でここへ漏れる**経路になりうる。
 *
 * 「気をつけて書く」では守れない — ログを 1 行足すだけで破れて、**破れたことは
 * 誰にも見えない**。よって**フィールド名の許可リストで構造的に落とす**。
 * 許可されていない名前は値ごと捨て、代わりに `dropped=<名前>` を出す
 * (黙って消すと「載せたつもり」と「落とされた」が区別できなくなる / CLAUDE.md)。
 *
 * 何を記録し何を落とすかの正典は [`docs/runbooks/bff-telemetry.md`](../../../../docs/runbooks/bff-telemetry.md)。
 *
 * ## 例外メッセージの扱い
 *
 * `errorMessage` は許可しているが、**payload を連結した文面を自分で作らないこと**。
 * zod の失敗は `schemaIssues.ts` の `summarizeIssues`(場所と種別だけ) を通す。
 * ここでは最後の防壁として 1 行化 + 300 文字で切るだけで、**切っても中身は漏れる**。
 */

import { AsyncLocalStorage } from "node:async_hooks";
import { createHash } from "node:crypto";

/** テレメトリの書き出し先。入口が違っても同じ形で出せるよう関数だけ受け取る。 */
export type TelemetryLogger = {
  log: (message: string) => void;
  error: (message: string) => void;
};

export type TelemetryValue = string | number | boolean | null | undefined;
export type TelemetryFields = Record<string, TelemetryValue>;

/**
 * テレメトリに載せてよいフィールド名。**ここに無い名前は値ごと捨てられる。**
 *
 * 足すときの判断基準: その値は「相談の中身」か「相談の形・経路・結果」か。
 * 形 (長さ・件数・ステータス・所要時間) と不透明な ID は載せてよい。
 * 本文・要約・抽出結果・認証トークン・クエリ文字列は載せない。
 */
const ALLOWED_FIELDS: ReadonlySet<string> = new Set([
  // -- 経路と結果 --
  "route",
  "method",
  "status",
  "outcome",
  "ms",
  // -- 下流ホップ (依存呼び出し) --
  "target",
  "operation",
  "url", // 値は redactUrl() を必ず通す (下の renderValue 参照)
  "upstreamStatus",
  // -- 相関に使う不透明な ID (中身を含まない) --
  "sessionId",
  "userHash", // 生の userId は載せない。hashIdentifier() の出力だけ
  // -- 量と種別 (中身ではなく形) --
  "chars",
  "count",
  "bytes",
  "speaker",
  "prefetch",
  "plan",
  "stubbed",
  "kind",
  "action",
  "reason",
  // -- 失敗 --
  "errorType",
  "errorMessage",
]);

/** `errorMessage` の上限。切っても安全にはならない (冒頭コメント参照) — 事故時の被害を減らすだけ。 */
const MAX_ERROR_MESSAGE_CHARS = 300;

/**
 * URL からクエリ文字列と userinfo を落として `origin + pathname` にする。
 *
 * 無いと何が静かに通るか: 下流の URL にクエリで本文やトークンを載せた瞬間、
 * それがそのままテレメトリに焼かれる。パース不能なものは**推測で通さない**。
 */
export function redactUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.origin}${parsed.pathname}`;
  } catch {
    // 握り潰しではない: 「URL として読めなかった」を値として残す。
    // 元文字列を出さないのは、壊れた URL にこそ本文が混ざりうるため。
    return "(unparsable-url)";
  }
}

/**
 * 個人を指す ID (userId 等) をテレメトリ用の不透明な値に変える。
 *
 * userId は Cosmos のパーティションキー = 「誰の相談か」そのもの。
 * 相関には必要だが平文で残す理由が無いので、片方向ハッシュの先頭だけ使う。
 */
export function hashIdentifier(value: string): string {
  return createHash("sha256").update(value).digest("hex").slice(0, 12);
}

function sanitizeScalar(text: string): string {
  return text.replace(/[\r\n\t]+/g, " ").trim();
}

function renderValue(key: string, value: string | number | boolean | null): string {
  if (value === null) return "null";
  if (typeof value !== "string") return String(value);

  // 許可した名前でも、値の形が決まっているものは**ここで**強制する。
  // 呼び出し側の作法に頼ると、1 箇所忘れただけで漏れる。
  const normalized =
    key === "url"
      ? redactUrl(value)
      : key === "errorMessage"
        ? sanitizeScalar(value).slice(0, MAX_ERROR_MESSAGE_CHARS)
        : sanitizeScalar(value);

  return /[\s="]/.test(normalized) ? JSON.stringify(normalized) : normalized;
}

/**
 * 1 行のテレメトリ行を組み立てる。**許可リストに無いフィールドは値ごと捨てる。**
 *
 * 戻り値は `event=... key=value ...` の 1 行。Log Analytics の
 * `FunctionAppLogs` / App Insights の `AppTraces` から `has` / `parse` で引ける形。
 */
export function formatTelemetryLine(event: string, fields: TelemetryFields = {}): string {
  const parts = [`event=${event}`];
  const dropped: string[] = [];

  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined) continue;
    if (!ALLOWED_FIELDS.has(key)) {
      dropped.push(key);
      continue;
    }
    parts.push(`${key}=${renderValue(key, value)}`);
  }

  // 落としたことを黙らせない。名前だけ出す (値は捨てたので出しようがない)。
  if (dropped.length > 0) parts.push(`dropped=${[...dropped].sort().join(",")}`);

  return parts.join(" ");
}

// -------------------- invocation スコープのロガー --------------------
//
// Azure Functions の Node v4 では `context.log` を使わないと**どの invocation の
// 話か紐づかない** (console.log は app-level ログとして拾われるだけ)。
// ところが下流クライアント (aiAgentClient / voicevoxClient / Cosmos) は
// InvocationContext を受け取らない位置にいる。引数で持ち回すと全シグネチャが
// 汚れるので、AsyncLocalStorage で invocation スコープに縛る。
//
// スコープの外 (ユニットテスト / スクリプト) では console にフォールバックする —
// 「ログが消える」より「invocation に紐づかないが出る」方がまし。

const loggerStorage = new AsyncLocalStorage<TelemetryLogger>();

const consoleTelemetryLogger: TelemetryLogger = {
  log: (message) => console.log(message),
  error: (message) => console.error(message),
};

/** `run` の中 (await の先も含む) で `currentLogger()` が `logger` を返すようにする。 */
export function runWithLogger<T>(logger: TelemetryLogger, run: () => T): T {
  return loggerStorage.run(logger, run);
}

/** 現在の invocation のロガー。スコープ外なら console。 */
export function currentLogger(): TelemetryLogger {
  return loggerStorage.getStore() ?? consoleTelemetryLogger;
}

export function logEvent(
  event: string,
  fields: TelemetryFields = {},
  logger: TelemetryLogger = currentLogger(),
): void {
  logger.log(formatTelemetryLine(event, fields));
}

export function logErrorEvent(
  event: string,
  fields: TelemetryFields = {},
  logger: TelemetryLogger = currentLogger(),
): void {
  logger.error(formatTelemetryLine(event, fields));
}

/** 例外を「種別 + 文面」に落とす。文面の扱いは冒頭コメントの規約に従う。 */
export function describeError(err: unknown): TelemetryFields {
  if (err instanceof Error) return { errorType: err.name, errorMessage: err.message };
  return { errorType: typeof err, errorMessage: String(err) };
}

// -------------------- 依存呼び出し (下流ホップ) --------------------

export type DependencySpec = {
  /** 呼び先の論理名 (`ai-agent` / `voicevox` / `cosmos`)。 */
  target: string;
  /** 呼び先の操作 (`POST /chat/stream` / `upsert`)。**中身ではなく操作名**。 */
  operation: string;
  /** 呼び先 URL (クエリは自動で落ちる)。 */
  url?: string;
  sessionId?: string;
};

/**
 * 下流ホップを **開始 / 終了 / 所要 ms / 結果** の 2 行で挟む。
 *
 * 無いと何が静かに通るか: 「呼んだが返ってこなかった」と「呼んですぐ失敗した」が
 * 区別できない。開始行だけが残っている状態が前者の証拠になる (#293 の本丸)。
 * 終了行だけ出す設計にすると、ハングは**沈黙**として現れ、正常時の沈黙と混ざる。
 */
export async function trackDependency<T>(
  spec: DependencySpec,
  run: () => Promise<T>,
  describeResult: (result: T) => TelemetryFields = () => ({}),
): Promise<T> {
  const logger = currentLogger();
  const startedAt = Date.now();

  logEvent("dependency.start", { ...spec }, logger);

  try {
    const result = await run();
    logEvent(
      "dependency.end",
      {
        target: spec.target,
        operation: spec.operation,
        sessionId: spec.sessionId,
        outcome: "success",
        ms: Date.now() - startedAt,
        ...describeResult(result),
      },
      logger,
    );
    return result;
  } catch (err) {
    logErrorEvent(
      "dependency.end",
      {
        target: spec.target,
        operation: spec.operation,
        sessionId: spec.sessionId,
        outcome: "failure",
        ms: Date.now() - startedAt,
        ...describeError(err),
      },
      logger,
    );
    throw err;
  }
}

// -------------------- リクエスト --------------------

/**
 * ハンドラ 1 回を **開始 / 終了 / status / 所要 ms** で挟み、同時に
 * invocation スコープのロガーを張る (下流の `currentLogger()` がこれを拾う)。
 *
 * SSE の注意: `handleChatStream` は**ストリームを開いた時点で return する**ので、
 * ここで測れるのは「開くまで」であって「流し切るまで」ではない。嘘をつかないよう
 * outcome を `stream-opened` にして区別する — 流し切ったかはホストの `Executed`
 * 行 / App Insights の `AppRequests` を見る (Runbook 参照)。
 */
export async function trackRequest(
  route: string,
  logger: TelemetryLogger,
  run: () => Promise<Response>,
  fields: TelemetryFields = {},
): Promise<Response> {
  return await runWithLogger(logger, async () => {
    const startedAt = Date.now();
    logEvent("request.start", { route, ...fields }, logger);

    try {
      const response = await run();
      const streaming = (response.headers.get("content-type") ?? "").includes("text/event-stream");
      logEvent(
        "request.end",
        {
          route,
          status: response.status,
          outcome: streaming ? "stream-opened" : "completed",
          ms: Date.now() - startedAt,
        },
        logger,
      );
      return response;
    } catch (err) {
      // ここに来るのは**ハンドラが握り損ねた例外** = 500 になる経路。
      // 記録してから再送出する (握り潰さない)。
      logErrorEvent(
        "request.end",
        { route, outcome: "threw", ms: Date.now() - startedAt, ...describeError(err) },
        logger,
      );
      throw err;
    }
  });
}
