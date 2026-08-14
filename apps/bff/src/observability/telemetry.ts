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
 * ただし許可リストは**名前しか見ない**ので、これだけでは足りない。許可された名前に
 * 本文を入れれば素通りする。よって値の側にも 2 つの門を置く:
 *
 *   1. 形が決まっている値は出口で正規化する — `url` → `redactUrl` / `errorMessage` → 1 行化 + 上限
 *   2. **相関 ID は出口で必ずハッシュ化する** — `HASHED_FIELDS`。`sessionId` は長さしか
 *      検証されない自由文字列なので、相関キーの顔をして本文を運べる (#413 のレビュー指摘)
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
  // -- 相関に使う ID は HASHED_FIELDS 側 (生では 1 つも通さない) --
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

/**
 * **値を必ずハッシュ化してから出す**フィールド。左が呼び出し側が渡す名前、右が行に出る名前。
 *
 * ここに載っている名前は `ALLOWED_FIELDS` には入れない — 生で通す道を残さないため。
 * 名前も `...Hash` に変えるのは、ログを読む人が「これは元の値ではない」と分かるようにするため
 * (`sessionId=8b1a…` だと生の ID に見えて、行を Cosmos の id と突き合わせようとする)。
 *
 * ## なぜ「呼び出し側でハッシュする」ではないのか (#413 レビュー指摘)
 *
 * `sessionId` は `z.string().min(1).max(MAX_ID_LENGTH)` = **長さしか見ていない自由文字列**で、
 * UUID を強制していない。認証済みクライアントが相談の本文をそのまま `sessionId` に入れれば、
 * 相関キーの顔をした本文が Application Insights に 30 日残る。これは `errorMessage` で踏んだ
 * 「許可された名前に本文を入れれば素通りする」と同じ穴で、代入点は
 * `/api/chat/stream` / tRPC (sendMessage・preview・extract) / 下流依存ログと**散っている**。
 *
 * 呼び出し側の作法 (`sessionId: hashIdentifier(x)`) に頼ると、1 箇所忘れただけで漏れ、
 * **忘れたことは誰にも見えない**。よって `url` → `redactUrl` と同じく、**出口で強制する**。
 */
const HASHED_FIELDS: ReadonlyMap<string, string> = new Map([
  // 相関には要るが、値の中身を信用しない (自由文字列)。
  ["sessionId", "sessionHash"],
  // Cosmos のパーティションキー = 「誰の相談か」そのもの。生で残す理由が無い。
  ["userId", "userHash"],
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
 * ID (userId / sessionId) をテレメトリ用の不透明な値に変える。
 *
 * userId は Cosmos のパーティションキー = 「誰の相談か」そのもの。sessionId は
 * 自由文字列で本文を入れられる。どちらも相関には必要だが平文で残す理由が無いので、
 * 片方向ハッシュの先頭だけ使う。
 *
 * **salt を入れない** — 入れるとプロセス間・再起動をまたいで同じ ID が別の値になり、
 * 相関 (この行とこの行は同じセッションか) が死ぬ。ここで守りたいのは秘匿性ではなく
 * 「本文をそのまま焼かない」こと。
 *
 * 呼び出し側からこれを直接呼ぶ必要は無い (出口の `HASHED_FIELDS` が強制する)。
 */
export function hashIdentifier(value: string): string {
  return createHash("sha256").update(value).digest("hex").slice(0, 12);
}

function sanitizeScalar(text: string): string {
  return text.replace(/[\r\n\t]+/g, " ").trim();
}

function renderValue(key: string, value: string | number | boolean | null): string {
  // null は「値が無い」の記録なのでハッシュ化しない (ハッシュすると欠測が定数に化ける)。
  if (value === null) return "null";

  // ハッシュ対象は**文字列以外で来ても**必ず潰す。型で逃げ道を作らない。
  if (HASHED_FIELDS.has(key)) return hashIdentifier(String(value));

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
 * 通るのは 2 種類だけ:
 *   - `ALLOWED_FIELDS` — そのまま出す (値の正規化は `renderValue` が担う)
 *   - `HASHED_FIELDS` — **必ずハッシュ化**し、名前も `...Hash` に付け替えて出す
 *
 * 戻り値は `event=... key=value ...` の 1 行。Log Analytics の
 * `FunctionAppLogs` / App Insights の `AppTraces` から `has` / `parse` で引ける形。
 */
export function formatTelemetryLine(event: string, fields: TelemetryFields = {}): string {
  const parts = [`event=${event}`];
  const dropped: string[] = [];

  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined) continue;
    const hashedName = HASHED_FIELDS.get(key);
    if (hashedName === undefined && !ALLOWED_FIELDS.has(key)) {
      dropped.push(key);
      continue;
    }
    parts.push(`${hashedName ?? key}=${renderValue(key, value)}`);
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
  /** 相関用のセッション ID。**行には `sessionHash=` としてハッシュだけが出る** (生では出ない)。 */
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
