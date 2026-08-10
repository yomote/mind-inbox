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
  ExtractFailed,
  createProblemPlan,
  extractMentions,
  loadProblem,
  loadProblems,
  sendMessage,
  startNewConsultation,
  triageProblem,
} from "../api";
import type { ConsultationSession, ExtractionResult, Problem, TriageInput } from "../api";
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
  createPlan: "次の一歩を作れませんでした。通信状況を確認して、もう一度お試しください。",
  openProblemList: "困りごと一覧を開けませんでした。通信状況を確認して、もう一度お試しください。",
  openProblem: "困りごとを開けませんでした。通信状況を確認して、もう一度お試しください。",
  problemNotFound: "その困りごとは見つかりませんでした。一覧を開き直してください。",
  triage: "困りごとを更新できませんでした。通信状況を確認して、もう一度お試しください。",
  dismiss: "却下できませんでした。通信状況を確認して、もう一度お試しください。",
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

  const startConsultation = React.useCallback(async () => {
    // テーマ入力画面は廃止 (home.mdx)。常に空で開始し、AI の問いかけから対話が始まる。
    const outcome = await runAction(FAILURE_MESSAGE.startConsultation, () =>
      startNewConsultation(""),
    );
    if (!outcome.ok) return;

    setSession(outcome.value);
    transition("session");
  }, [runAction, transition]);

  const sendDraftMessage = React.useCallback(async () => {
    if (!session || !draftMessage.trim() || loadingRef.current) return;

    const userMessage = {
      id: `u-${Date.now()}`,
      role: "user" as const,
      text: draftMessage.trim(),
      createdAt: new Date().toISOString(),
    };

    setDraftMessage("");
    // 最初のユーザー発話でタイトルを内容から自動生成 (開始時は聞かない)。
    const isFirstUserMessage = !session.messages.some((m) => m.role === "user");
    const nextTitle = isFirstUserMessage ? deriveSessionTitle(userMessage.text) : session.title;
    setSession({ ...session, title: nextTitle, messages: [...session.messages, userMessage] });

    const outcome = await runAction(FAILURE_MESSAGE.sendMessage, () =>
      sendMessage(session.id, userMessage.text),
    );

    if (!outcome.ok) {
      // 楽観更新を巻き戻して送信前の状態に戻す。返事が来ないまま自分の発話だけが
      // 残る状態は「送れたのに無視された」と読めてしまい、かつ入力し直しを強いる。
      setSession(session);
      setDraftMessage(userMessage.text);
      return;
    }

    setSession((prev) => (prev ? { ...prev, messages: [...prev.messages, outcome.value] } : prev));
  }, [draftMessage, runAction, session]);

  const extract = React.useCallback(async () => {
    if (!session) return;
    // 会話はこちらが持っているので渡す (#183)。ai-agent のセッション履歴はプロセスメモリで、
    // scale-to-zero・スケールアウト・リビジョン差し替えのいずれでも消えるため、
    // それに依存すると「対話はできたのに抽出だけ 404」になる。
    const outcome = await runAction(extractFailureMessage, () =>
      extractMentions(session.id, session.messages),
    );
    if (!outcome.ok) return;

    setExtraction(outcome.value);
    transition("extractReview");
  }, [runAction, session, transition]);

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
  }, [setBusy]);

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
