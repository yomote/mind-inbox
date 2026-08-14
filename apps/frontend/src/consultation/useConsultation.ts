/**
 * 相談フロー (吐き出し → 抽出 → 困りごと → 次の一歩) の状態と操作を所有する hook。
 *
 * 分離の理由 (#142): 元は Layout.tsx が相談ドメインの state 9 とハンドラ 12 を抱え、
 * 相談フローを 1 行変えるのに 900 行のシェル (認証 / AppBar / 読み上げ) ごと読む必要があった。
 * ここに閉じたことで「相談の変更 = この 1 ファイル」「認証・シェルの変更 = Layout」になる。
 *
 * 境界: 通信の可否・mock/real の切り替えは api 層 (`src/api/`) の責務。この hook は
 * 「api の戻りをどう画面状態に反映し、どこへ遷移するか」だけを持つ。
 */

import * as React from "react";
import {
  ApprovalRequestUnusable,
  ExtractFailed,
  commitPreview,
  createProblemPlan,
  extractMentions,
  loadProblem,
  loadProblems,
  previewExtraction,
  previewSupported,
  respondToApproval,
  sendMessage,
  startNewConsultation,
  triageProblem,
} from "../api";
import type {
  ApprovalRequest,
  ChatMessage,
  ConsultationSession,
  ExtractionResult,
  Problem,
  TriageInput,
} from "../api";
import type { AppRoute } from "../Router";
import { deriveSessionTitle } from "./sessionTitle";

export type Consultation = {
  loading: boolean;
  /** 無音失敗を出さないためのユーザー向けエラー (Snackbar 表示)。 */
  actionError: string | null;
  clearActionError: () => void;

  draftMessage: string;
  setDraftMessage: (value: string) => void;

  session: ConsultationSession | null;
  extraction: ExtractionResult | null;
  problems: Problem[];
  selectedProblem: Problem | null;

  /**
   * 「整理されつつある困りごと」の揮発する下書き (#187 / ADR 0039 D1)。
   * 読み取り専用 — 確定 (extract) して初めて Problem リポジトリに書かれる。
   */
  preview: ExtractionResult | null;
  /** error は右ペイン内に表示する (会話を止めない — Snackbar には出さない)。 */
  previewStatus: "idle" | "updating" | "error";
  /** 手動「今すぐ整理」(ADR 0039 D2)。 */
  refreshPreview: () => void;

  /**
   * 副作用ツールの実行前に人間の承認を待っている要求 (#82 / G1 / §5.9)。
   * null = 承認待ちなし。
   */
  pendingApproval: ApprovalRequest | null;
  /** 承認 (true) / 却下 (false) をサーバへ返す。 */
  respondToPendingApproval: (approved: boolean) => Promise<void>;

  startConsultation: () => Promise<void>;
  sendDraftMessage: () => Promise<void>;
  extract: () => Promise<void>;
  openProblemList: () => Promise<void>;
  openProblem: (id: string) => Promise<void>;
  triage: (input: TriageInput) => Promise<void>;
  dismissExtracted: (problemId: string) => Promise<void>;
  createPlanForProblem: (problemId: string) => Promise<void>;
  /** ログアウト時に相談の状態を捨てる。 */
  reset: () => void;
};

/**
 * 操作ごとのユーザー向け失敗文言 (ADR 0018「無音失敗の禁止」)。
 *
 * ここを 1 箇所に集めているのは、**ハンドラを足したときに文言を書き忘れると
 * 型エラーになる**ようにするため。実環境で「ボタンを押しても何も起きない」
 * (2026-08-09 に PO が踏んだ症状) は、例外を握らないハンドラが 1 つでもあれば再発する。
 */
const FAILURE_MESSAGE = {
  startConsultation: "相談を開始できませんでした。通信状況を確認して、もう一度お試しください。",
  sendMessage: "メッセージを送れませんでした。入力はそのまま残っています。もう一度お試しください。",
  extract: "困りごとを抽出できませんでした。通信状況を確認して、もう一度お試しください。",
  commitPreview:
    "下書きを確定できませんでした。下書きは残っています。通信状況を確認して、もう一度お試しください。",
  createPlan: "次の一歩を作れませんでした。通信状況を確認して、もう一度お試しください。",
  openProblemList: "困りごと一覧を開けませんでした。通信状況を確認して、もう一度お試しください。",
  openProblem: "困りごとを開けませんでした。通信状況を確認して、もう一度お試しください。",
  problemNotFound: "その困りごとは見つかりませんでした。一覧を開き直してください。",
  triage: "困りごとを更新できませんでした。通信状況を確認して、もう一度お試しください。",
  dismiss: "却下できませんでした。通信状況を確認して、もう一度お試しください。",
  approval:
    "承認の結果を送れませんでした。操作はまだ実行されていません。通信状況を確認して、もう一度お試しください。",
} as const;

/**
 * 抽出の失敗は原因で案内を変える (#183)。
 *
 * 「抽出できませんでした」だけだと、通信の問題なのか・相談の内容が失われたのか・
 * AI 側が壊れているのかが分からず、ユーザーは次に何をすればいいか判断できない。
 * 種別の判定は api 層 (`ExtractFailed.kind`)、文面はここ = UI の持ち場。
 */
function extractFailureMessage(err: unknown): string {
  if (!(err instanceof ExtractFailed)) return FAILURE_MESSAGE.extract;
  switch (err.kind) {
    case "session-missing":
      return "この相談の内容を取り出せませんでした。お手数ですが、もう一度お試しください。";
    case "llm-parse-failed":
      return "困りごとの整理に失敗しました。相談の内容は残っています。もう一度お試しください。";
    default:
      return FAILURE_MESSAGE.extract;
  }
}

/**
 * 送信の失敗は原因で案内を変える (#82 / PR #416 Codex P2)。
 *
 * 「承認が要ると言われたのに承認できない応答」は通信の問題ではないので、
 * 「もう一度お試しください」だけだと何度やっても同じところで止まる。
 * **承認不要 (普通の返事) と同じ扱いにしない**のがこの分岐の目的。
 */
function sendMessageFailureMessage(err: unknown): string {
  if (err instanceof ApprovalRequestUnusable) {
    return "AI が操作の承認を求めましたが、承認できない応答が返りました。実行はされていません。もう一度お試しください。";
  }
  return FAILURE_MESSAGE.sendMessage;
}

/** runAction の結果。失敗しても throw せず、呼び出し側が後続処理を止められるようにする。 */
type ActionOutcome<T> = { ok: true; value: T } | { ok: false };

/**
 * @param transition 画面遷移。認証ガードを持つ Layout 側の実装を受け取る
 *                   (遷移の可否判断は Layout の責務なのでここでは持たない)。
 *
 * **マウント時に API を呼ばないこと** — 未認証で叩くと getAccessToken() が
 * ログインリダイレクトを誘発し、オンボーディングを見せないまま Entra へ飛ばす
 * (#112 / onboarding.mdx)。データはすべてユーザー操作を起点に読む。
 */
export function useConsultation(transition: (next: AppRoute) => void): Consultation {
  const [loading, setLoading] = React.useState(false);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [draftMessage, setDraftMessage] = React.useState("");

  const [session, setSession] = React.useState<ConsultationSession | null>(null);
  const [extraction, setExtraction] = React.useState<ExtractionResult | null>(null);
  const [problems, setProblems] = React.useState<Problem[]>([]);
  const [selectedProblem, setSelectedProblem] = React.useState<Problem | null>(null);

  // 承認待ちの副作用ツール (#82 / G1 / §5.9)。**既定は「実行しない」** — ここが
  // null に戻る経路 (承認 / 却下 / 次の発話 / セッション破棄) はどれもツールを
  // 実行しない側に倒れる。実行が起きるのは respondToPendingApproval(true) だけ。
  const [pendingApproval, setPendingApproval] = React.useState<ApprovalRequest | null>(null);

  const [preview, setPreview] = React.useState<ExtractionResult | null>(null);
  const [previewStatus, setPreviewStatus] = React.useState<"idle" | "updating" | "error">("idle");
  // preview の世代トークン (PR #282 Codex P2): セッションが変わったら +1 し、飛行中だった
  // 古い応答を無条件に破棄する。preview は 600ms 級 / 相談開始は 350ms 級で、
  // 「今すぐ整理 → 中断 → 新規相談」で旧セッションの下書きが新セッションに出る競合が実在する。
  const previewGenerationRef = React.useRef(0);
  // preview は会話 (loading / runAction) と独立に走る。多重実行だけ自前で防ぐ
  // (ADR 0039 D2: in-flight 1 本 — LLM 呼び出しを重ねない)。世代で持つのは、
  // 旧セッションの飛行中リクエストが新セッションの初回 preview を塞がないようにするため。
  const previewInFlightGenerationRef = React.useRef<number | null>(null);
  // 実行中に届いた更新要求の持ち越し先 (PR #282 再レビュー P2)。**最新の 1 件だけ**持つ —
  // 中間状態の下書きを順に流しても意味がなく、LLM 呼び出しだけが増えるため。
  const pendingPreviewRef = React.useRef<{ sessionId: string; messages: ChatMessage[] } | null>(
    null,
  );

  /** 下書きを揮発させ、飛行中の preview 応答を無効化する (セッション跨ぎ / reset)。 */
  const invalidatePreview = React.useCallback(() => {
    previewGenerationRef.current += 1;
    pendingPreviewRef.current = null;
    setPreview(null);
    setPreviewStatus("idle");
  }, []);

  /**
   * 表示中の下書きを固定する (PR #282 再レビュー P1)。
   *
   * 確定は「今画面に出ている内容」を保存する操作なので、確定の最中に飛行中の更新が
   * 返ってきて表示だけ差し替わると、**保存した内容と画面の内容がずれる**
   * (preview 600ms / commit 400ms で実際に起きる)。確定開始時に世代を進めて
   * 飛行中の応答を捨て、表示はそのまま (= 保存する内容と一致) に保つ。
   * `invalidatePreview` と違い**下書きは消さない** — 確定が失敗しても画面は変わらない。
   */
  const freezePreview = React.useCallback(() => {
    previewGenerationRef.current += 1;
    // 確定中に届いていた自動更新の要求は繰り越さない — 確定が成功すれば下書きは揮発する
    // ので更新しても捨てるだけ、失敗した場合は「押した時点の内容」を残す方が確定操作の
    // 約束 (この内容で確定) と一致するため。
    pendingPreviewRef.current = null;
    // 破棄した更新のスピナーを残さない (更新は起きなかったことになる)。
    setPreviewStatus("idle");
  }, []);

  // loading は多重実行ガードにだけ使うので、ハンドラの identity を安定させるため ref でも持つ。
  const loadingRef = React.useRef(false);
  const setBusy = React.useCallback((next: boolean) => {
    loadingRef.current = next;
    setLoading(next);
  }, []);

  /**
   * すべての非同期操作の唯一の入口 (ADR 0018「無音失敗の禁止」)。
   *
   * - 例外を握り潰さず、必ず `actionError` (Snackbar) に落とす
   * - 多重実行を 1 箇所で防ぐ (連打・ダブルタップ)
   * - 失敗時は `{ ok: false }` を返し、**呼び出し側が画面遷移をしない**ようにする
   *
   * ここを通さない fetch を書くと「押しても何も起きない」が復活する。新しい操作を
   * 足すときは必ずこのラッパを通すこと。
   */
  const runAction = React.useCallback(
    async <T>(
      // 原因によって案内を変えたい操作があるので関数も受ける (#183)。
      // 固定文面で足りる大多数はそのまま string を渡せばよい。
      failureMessage: string | ((err: unknown) => string),
      action: () => Promise<T>,
    ): Promise<ActionOutcome<T>> => {
      if (loadingRef.current) return { ok: false };
      setBusy(true);
      try {
        return { ok: true, value: await action() };
      } catch (err) {
        const message = typeof failureMessage === "function" ? failureMessage(err) : failureMessage;
        console.error(`[useConsultation] ${message}`, err);
        setActionError(message);
        return { ok: false };
      } finally {
        setBusy(false);
      }
    },
    [setBusy],
  );

  /**
   * 下書きプレビューを更新する (#187 / ADR 0039)。
   *
   * runAction を通さない: 失敗しても会話は続けられる背景処理なので、Snackbar と
   * 多重実行ガード (loading) を会話と共有しない。ただし**無音にはしない** —
   * 失敗は previewStatus="error" として右ペイン内に表示する (ADR 0018)。
   *
   * 実行中に届いた要求は**捨てずに最新 1 件だけ持ち越し、完了後に走らせる**
   * (PR #282 再レビュー P2)。捨てると「整理中に 2 往復目を送ると自動更新の契機が
   * 消え、手動で押すか更に 2 往復するまで下書きが古いまま」になり、§5.8 の
   * 「2 往復ごとに更新」が満たされなくなる。重ねて実行はしない (LLM 呼び出しは 1 本)。
   */
  const runPreview = React.useCallback(async (sessionId: string, messages: ChatMessage[]) => {
    if (!previewSupported) return;
    const generation = previewGenerationRef.current;
    // in-flight 1 本 (ADR 0039 D2)。世代で比較するのは、旧セッションの飛行中リクエストが
    // 新セッションの初回 preview を塞がないようにするため。
    if (previewInFlightGenerationRef.current === generation) {
      pendingPreviewRef.current = { sessionId, messages };
      return;
    }
    previewInFlightGenerationRef.current = generation;
    try {
      let request: { sessionId: string; messages: ChatMessage[] } | null = { sessionId, messages };
      while (request) {
        setPreviewStatus("updating");
        try {
          const result = await previewExtraction(request.sessionId, request.messages);
          // 応答が返るまでにセッションが変わっていたら捨てる (PR #282 Codex P2)。
          // 旧セッションの下書きを新セッションに出すと「確定すると関係ない困りごとが
          // 保存される」誤操作の入口になる。
          if (previewGenerationRef.current !== generation) return;
          setPreview(result);
          setPreviewStatus("idle");
        } catch (err) {
          if (previewGenerationRef.current !== generation) return;
          console.error("[useConsultation] 下書きプレビューの更新に失敗", err);
          setPreviewStatus("error");
        }

        // 実行中に会話が進んでいたら、最新の会話で続けて 1 回だけ走らせる。
        // 世代が変わっていれば (セッション切替 / 確定) 持ち越しは無効なので捨てる。
        request = pendingPreviewRef.current;
        pendingPreviewRef.current = null;
        if (previewGenerationRef.current !== generation) request = null;
      }
    } finally {
      if (previewInFlightGenerationRef.current === generation) {
        previewInFlightGenerationRef.current = null;
      }
    }
  }, []);

  const refreshPreview = React.useCallback(() => {
    if (!session) return;
    void runPreview(session.id, session.messages);
  }, [runPreview, session]);

  const startConsultation = React.useCallback(async () => {
    // テーマ入力画面は廃止 (home.mdx)。常に空で開始し、AI の問いかけから対話が始まる。
    const outcome = await runAction(FAILURE_MESSAGE.startConsultation, () =>
      startNewConsultation(""),
    );
    if (!outcome.ok) return;

    setSession(outcome.value);
    // 前セッションの下書きを持ち込まない + 飛行中の旧応答を無効化する
    // (下書きはセッション内で揮発 — ADR 0039 / PR #282 Codex P2)。
    invalidatePreview();
    // 前セッションの承認要求も持ち込まない (別の会話の実行確認を新しい会話で押させない)。
    setPendingApproval(null);
    transition("session");
  }, [invalidatePreview, runAction, transition]);

  const sendDraftMessage = React.useCallback(async () => {
    if (!session || !draftMessage.trim() || loadingRef.current) return;

    const userMessage = {
      id: `u-${Date.now()}`,
      role: "user" as const,
      text: draftMessage.trim(),
      createdAt: new Date().toISOString(),
    };

    // 承認待ちのまま会話を続ける = **却下** (§5.9 / PR #416 Codex P2)。
    //
    // ローカル state を消すだけにすると、ai-agent 側の `ApprovalRecord` と checkpoint は
    // pending のまま残る (in-memory 構成の `_pending_run_storages` には TTL が無いので、
    // 繰り返すほど保持領域が増える)。「実行しない」はサーバにも伝える。
    // 却下が届かなかったら**発話を送らずここで止める** — 入力もカードも残るので、
    // やり直すか承認/却下ボタンを押し直せる (握り潰して次へ進まない / ADR 0018)。
    let baseSession = session;
    if (pendingApproval) {
      const discarded = await runAction(FAILURE_MESSAGE.approval, () =>
        respondToApproval(pendingApproval.id, false),
      );
      if (!discarded.ok) return;

      baseSession = { ...baseSession, messages: [...baseSession.messages, discarded.value] };
      setPendingApproval(null);
      setSession(baseSession);
    }

    setDraftMessage("");
    // 最初のユーザー発話でタイトルを内容から自動生成 (開始時は聞かない)。
    const isFirstUserMessage = !baseSession.messages.some((m) => m.role === "user");
    const nextTitle = isFirstUserMessage ? deriveSessionTitle(userMessage.text) : baseSession.title;
    setSession({
      ...baseSession,
      title: nextTitle,
      messages: [...baseSession.messages, userMessage],
    });

    const outcome = await runAction(sendMessageFailureMessage, () =>
      sendMessage(baseSession.id, userMessage.text),
    );

    if (!outcome.ok) {
      // 楽観更新を巻き戻して送信前の状態に戻す。返事が来ないまま自分の発話だけが
      // 残る状態は「送れたのに無視された」と読めてしまい、かつ入力し直しを強いる。
      // (却下は既に成立しているので、その結果は残したまま戻す)
      setSession(baseSession);
      setDraftMessage(userMessage.text);
      return;
    }

    setSession((prev) =>
      prev ? { ...prev, messages: [...prev.messages, outcome.value.message] } : prev,
    );
    // 承認要求は応答と同じ往復で届く (#82)。ここで拾わないと、サーバは承認待ちのまま
    // 画面には普通の返事だけが出て「止まっているのに止まって見えない」状態になる。
    setPendingApproval(outcome.value.approval);

    // ユーザー発話 2 往復ごとに下書きプレビューを自動更新する (#187 / ADR 0039 D2)。
    // 毎ターンは LLM 呼び出しが倍増するため間引く。fire-and-forget — 会話を待たせない。
    const messagesAfterReply = [...baseSession.messages, userMessage, outcome.value.message];
    const userTurnCount = messagesAfterReply.filter((m) => m.role === "user").length;
    if (userTurnCount > 0 && userTurnCount % 2 === 0) {
      void runPreview(baseSession.id, messagesAfterReply);
    }
  }, [draftMessage, pendingApproval, runAction, runPreview, session]);

  /**
   * 承認 / 却下をサーバへ返す (#82 / G1 / §5.9)。
   *
   * **却下も必ず送る** — 送らないとサーバ側の承認待ち (checkpoint) が宙に浮く。
   * 失敗したら要求を消さない (もう一度押せる)。ここで消すと「却下したつもりが
   * サーバには届いておらず、承認待ちが残っている」状態を画面から隠してしまう。
   */
  const respondToPendingApproval = React.useCallback(
    async (approved: boolean) => {
      if (!pendingApproval) return;

      const outcome = await runAction(FAILURE_MESSAGE.approval, () =>
        respondToApproval(pendingApproval.id, approved),
      );
      if (!outcome.ok) return;

      setPendingApproval(null);
      // 承認・却下どちらの結果もガイドの発話として会話に残す (何が起きたかを消さない)。
      setSession((prev) =>
        prev ? { ...prev, messages: [...prev.messages, outcome.value] } : prev,
      );
    },
    [pendingApproval, runAction],
  );

  const extract = React.useCallback(async () => {
    if (!session) return;

    // プレビュー有効環境では「この内容で確定」= **表示中の下書きをそのまま永続化する**
    // (ADR 0039 D1・D3 / PR #282 Codex P1)。抽出は非決定的なので、確定時に抽出し直すと
    // 画面で確認した内容と違うものが保存されうる — 再抽出はしない。
    if (previewSupported) {
      // 下書きが無い間はボタンが disabled (dialogue-session.mdx §5.8)。二重ガード。
      // **ここで従来の一発抽出に落とさない** — 「この内容で確定」が「今から抽出し直す」に
      // 化けると、画面で確認していない内容が保存されうる (PR #282 Codex P1 の否決理由)。
      // 更新が失敗し続けているときの復帰導線は「今すぐ整理」の再実行 (§5.8)。
      if (!preview) return;
      // 確定するのは**押した時点で画面に出ている内容**。飛行中の更新はここで捨て、
      // 保存対象をスナップショットとして固定する (PR #282 再レビュー P1)。
      const snapshot = preview;
      freezePreview();
      const outcome = await runAction(FAILURE_MESSAGE.commitPreview, () =>
        commitPreview(session.id, snapshot.items),
      );
      if (!outcome.ok) return;

      setExtraction(outcome.value);
      // 確定済みの下書きを「未確定の下書き」として残さない (揮発 — ADR 0039 D1)。
      invalidatePreview();
      transition("extractReview");
      return;
    }

    // プレビュー無効環境 (BFF `consultation.preview` 未結線の real — #283 で結線) は
    // 従来の一発抽出。会話はこちらが持っているので渡す (#183)。ai-agent のセッション履歴は
    // プロセスメモリで、scale-to-zero・スケールアウト・リビジョン差し替えのいずれでも
    // 消えるため、それに依存すると「対話はできたのに抽出だけ 404」になる。
    const outcome = await runAction(extractFailureMessage, () =>
      extractMentions(session.id, session.messages),
    );
    if (!outcome.ok) return;

    setExtraction(outcome.value);
    transition("extractReview");
  }, [freezePreview, invalidatePreview, preview, runAction, session, transition]);

  const openProblemList = React.useCallback(async () => {
    const outcome = await runAction(FAILURE_MESSAGE.openProblemList, () => loadProblems());
    if (!outcome.ok) return;

    setProblems(outcome.value);
    transition("problemList");
  }, [runAction, transition]);

  const openProblem = React.useCallback(
    async (id: string) => {
      const outcome = await runAction(FAILURE_MESSAGE.openProblem, () => loadProblem(id));
      if (!outcome.ok) return;

      if (!outcome.value) {
        // 「開けたはずのカードを押したのに何も起きない」を作らない。一覧が古い
        // (別セッションで消えた / リサイクルで失われた) ことを言葉にして返す。
        setActionError(FAILURE_MESSAGE.problemNotFound);
        return;
      }

      setSelectedProblem(outcome.value);
      transition("problemDetail");
    },
    [runAction, transition],
  );

  const triage = React.useCallback(
    async (input: TriageInput) => {
      const outcome = await runAction(FAILURE_MESSAGE.triage, async () => {
        const affected = await triageProblem(input);
        // 一覧キャッシュを最新化 (棚卸し / 却下 / 統合がそのまま反映されるように)。
        return { affected, problems: await loadProblems() };
      });
      if (!outcome.ok) return;

      setSelectedProblem(outcome.value.affected);
      setProblems(outcome.value.problems);
      if (input.action === "dismiss" || input.action === "merge") {
        // 対象が消えたので一覧へ戻す。
        transition("problemList");
      }
    },
    [runAction, transition],
  );

  const dismissExtracted = React.useCallback(
    async (problemId: string) => {
      const outcome = await runAction(FAILURE_MESSAGE.dismiss, () =>
        triageProblem({ action: "dismiss", problemId }),
      );
      if (!outcome.ok) return;

      setExtraction((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.filter((item) => item.grouping.problemId !== problemId),
              newProblemCount: Math.max(0, prev.newProblemCount - 1),
            }
          : prev,
      );
    },
    [runAction],
  );

  const createPlanForProblem = React.useCallback(
    async (problemId: string) => {
      const outcome = await runAction(FAILURE_MESSAGE.createPlan, () =>
        createProblemPlan(problemId),
      );
      if (!outcome.ok) return;

      if (!outcome.value) {
        setActionError(FAILURE_MESSAGE.problemNotFound);
        return;
      }
      setSelectedProblem(outcome.value);
    },
    [runAction],
  );

  const clearActionError = React.useCallback(() => setActionError(null), []);

  const reset = React.useCallback(() => {
    setBusy(false);
    setActionError(null);
    setDraftMessage("");
    setSession(null);
    setExtraction(null);
    setProblems([]);
    setSelectedProblem(null);
    setPendingApproval(null);
    invalidatePreview();
  }, [invalidatePreview, setBusy]);

  return {
    loading,
    actionError,
    clearActionError,
    draftMessage,
    setDraftMessage,
    session,
    extraction,
    problems,
    selectedProblem,
    preview,
    previewStatus,
    refreshPreview,
    pendingApproval,
    respondToPendingApproval,
    startConsultation,
    sendDraftMessage,
    extract,
    openProblemList,
    openProblem,
    triage,
    dismissExtracted,
    createPlanForProblem,
    reset,
  };
}
