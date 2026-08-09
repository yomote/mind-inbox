/**
 * 相談フロー (吐き出し → 抽出 → 困りごと → プラン → 履歴) の状態と操作を所有する hook。
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
  createActionPlan,
  createProblemPlan,
  extractMentions,
  loadHistories,
  loadProblem,
  loadProblems,
  organizeResult,
  saveHistory,
  sendMessage,
  startNewConsultation,
  triageProblem,
} from "../api";
import type {
  ActionPlan,
  ConsultationSession,
  ExtractionResult,
  HistoryItem,
  OrganizedResult,
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
  result: OrganizedResult | null;
  plan: ActionPlan | null;
  histories: HistoryItem[];
  selectedHistory: HistoryItem | null;
  extraction: ExtractionResult | null;
  problems: Problem[];
  selectedProblem: Problem | null;

  startConsultation: () => Promise<void>;
  sendDraftMessage: () => Promise<void>;
  organize: () => Promise<void>;
  extract: () => Promise<void>;
  createPlan: () => Promise<void>;
  saveAndGoHistory: () => Promise<void>;
  openHistoryResult: (item: HistoryItem) => void;
  openProblemList: () => Promise<void>;
  openProblem: (id: string) => Promise<void>;
  triage: (input: TriageInput) => Promise<void>;
  dismissExtracted: (problemId: string) => Promise<void>;
  createPlanForProblem: (problemId: string) => Promise<void>;
  /** ログアウト時に相談の状態を捨てる。 */
  reset: () => void;
};

/**
 * 抽出の失敗をユーザー向けの一文にする (#183)。
 *
 * 「失敗した」だけでは何をすればいいか分からないので、**次の動作**まで含める。
 * 種別の判断は API 層 (ExtractFailed.kind)、文面はここ = UI の持ち場。
 */
function extractErrorMessage(err: unknown): string {
  const kind = err instanceof ExtractFailed ? err.kind : "unknown";
  switch (kind) {
    case "session-missing":
      return "この相談の内容を取り出せませんでした。お手数ですが、もう一度お試しください。";
    case "llm-parse-failed":
      return "困りごとの整理に失敗しました。相談の内容は残っています。もう一度お試しください。";
    case "upstream-failed":
      return "困りごとを抽出できませんでした。通信状況を確認して、もう一度お試しください。";
    default:
      return "困りごとを抽出できませんでした。もう一度お試しください。";
  }
}

export type ConsultationOptions = {
  /**
   * データ読み込みを始めてよいか (= 認証が確定したか)。
   * 未認証で API を呼ぶと getAccessToken() がログインリダイレクトを誘発し、
   * オンボーディングを見せないまま Entra へ飛ばしてしまう (#112 / onboarding.mdx)。
   * 認証の判断は Layout の責務なので、hook は「読んでよいか」だけを受け取る。
   */
  ready: boolean;
};

/**
 * @param transition 画面遷移。認証ガードを持つ Layout 側の実装を受け取る
 *                   (遷移の可否判断は Layout の責務なのでここでは持たない)。
 */
export function useConsultation(
  transition: (next: AppRoute) => void,
  { ready }: ConsultationOptions,
): Consultation {
  const [loading, setLoading] = React.useState(false);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [draftMessage, setDraftMessage] = React.useState("");

  const [session, setSession] = React.useState<ConsultationSession | null>(null);
  const [result, setResult] = React.useState<OrganizedResult | null>(null);
  const [plan, setPlan] = React.useState<ActionPlan | null>(null);
  const [histories, setHistories] = React.useState<HistoryItem[]>([]);
  const [selectedHistory, setSelectedHistory] = React.useState<HistoryItem | null>(null);
  const [extraction, setExtraction] = React.useState<ExtractionResult | null>(null);
  const [problems, setProblems] = React.useState<Problem[]>([]);
  const [selectedProblem, setSelectedProblem] = React.useState<Problem | null>(null);

  // loading は多重実行ガードにだけ使うので、ハンドラの identity を安定させるため ref でも持つ。
  const loadingRef = React.useRef(false);
  const setBusy = React.useCallback((next: boolean) => {
    loadingRef.current = next;
    setLoading(next);
  }, []);

  React.useEffect(() => {
    if (!ready) return;

    let active = true;
    void (async () => {
      const data = await loadHistories();
      if (active) setHistories(data);
    })();

    return () => {
      active = false;
    };
  }, [ready]);

  const startConsultation = React.useCallback(async () => {
    // ホームのボタンに直結しているため、連打・ダブルタップでの並行実行をここで防ぐ。
    if (loadingRef.current) return;
    setBusy(true);
    try {
      // テーマ入力画面は廃止 (home.mdx)。常に空で開始し、AI の問いかけから対話が始まる。
      const newSession = await startNewConsultation("");
      setSession(newSession);
      setResult(null);
      setPlan(null);
      setSelectedHistory(null);
      transition("session");
    } catch (err) {
      // 無音失敗の禁止: 開始に失敗したことと次の動作を必ずユーザーに見せる (ADR 0018)。
      console.error("[startConsultation]", err);
      setActionError("相談を開始できませんでした。通信状況を確認して、もう一度お試しください。");
    } finally {
      setBusy(false);
    }
  }, [setBusy, transition]);

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

    setBusy(true);
    try {
      const assistantMessage = await sendMessage(session.id, userMessage.text);
      setSession((prev) =>
        prev ? { ...prev, messages: [...prev.messages, assistantMessage] } : prev,
      );
    } finally {
      setBusy(false);
    }
  }, [draftMessage, session, setBusy]);

  const organize = React.useCallback(async () => {
    if (!session || loadingRef.current) return;
    setBusy(true);
    try {
      setResult(await organizeResult(session.id));
      transition("result");
    } finally {
      setBusy(false);
    }
  }, [session, setBusy, transition]);

  const extract = React.useCallback(async () => {
    if (!session || loadingRef.current) return;
    setBusy(true);
    try {
      // 会話はこちらが持っているので渡す (#183)。ai-agent のプロセスメモリに賭けない。
      setExtraction(await extractMentions(session.id, session.messages));
      transition("extractReview");
    } catch (err) {
      // 無音失敗の禁止 (ADR 0018)。以前はここに catch が無く、失敗すると遷移もせず
      // スピナーが止まるだけで、押した本人には何が起きたのか一切分からなかった (#183)。
      console.error("[extract]", err);
      setActionError(extractErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [session, setBusy, transition]);

  const createPlan = React.useCallback(async () => {
    if (!result || loadingRef.current) return;
    setBusy(true);
    try {
      setPlan(await createActionPlan(result));
      transition("actionPlan");
    } finally {
      setBusy(false);
    }
  }, [result, setBusy, transition]);

  const saveAndGoHistory = React.useCallback(async () => {
    if (!result || !plan || !session) return;
    setBusy(true);
    try {
      const item = await saveHistory({
        sessionId: session.id,
        title: session.title || "相談履歴",
        result,
        plan,
      });
      setHistories((prev) => [item, ...prev]);
      setSelectedHistory(item);
      transition("history");
    } finally {
      setBusy(false);
    }
  }, [plan, result, session, setBusy, transition]);

  const openHistoryResult = React.useCallback(
    (item: HistoryItem) => {
      setSelectedHistory(item);
      setResult(item.result);
      transition("result");
    },
    [transition],
  );

  const openProblemList = React.useCallback(async () => {
    setBusy(true);
    try {
      setProblems(await loadProblems());
      transition("problemList");
    } finally {
      setBusy(false);
    }
  }, [setBusy, transition]);

  const openProblem = React.useCallback(
    async (id: string) => {
      setBusy(true);
      try {
        const found = await loadProblem(id);
        if (found) {
          setSelectedProblem(found);
          transition("problemDetail");
        }
      } finally {
        setBusy(false);
      }
    },
    [setBusy, transition],
  );

  const triage = React.useCallback(
    async (input: TriageInput) => {
      if (loadingRef.current) return;
      setBusy(true);
      try {
        setSelectedProblem(await triageProblem(input));
        // 一覧キャッシュを最新化 (棚卸し / 却下 / 統合がそのまま反映されるように)。
        setProblems(await loadProblems());
        if (input.action === "dismiss" || input.action === "merge") {
          // 対象が消えたので一覧へ戻す。
          transition("problemList");
        }
      } finally {
        setBusy(false);
      }
    },
    [setBusy, transition],
  );

  const dismissExtracted = React.useCallback(
    async (problemId: string) => {
      if (loadingRef.current) return;
      setBusy(true);
      try {
        await triageProblem({ action: "dismiss", problemId });
        setExtraction((prev) =>
          prev
            ? {
                ...prev,
                items: prev.items.filter((item) => item.grouping.problemId !== problemId),
                newProblemCount: Math.max(0, prev.newProblemCount - 1),
              }
            : prev,
        );
      } finally {
        setBusy(false);
      }
    },
    [setBusy],
  );

  const createPlanForProblem = React.useCallback(
    async (problemId: string) => {
      if (loadingRef.current) return;
      setBusy(true);
      try {
        const updated = await createProblemPlan(problemId);
        if (updated) setSelectedProblem(updated);
      } finally {
        setBusy(false);
      }
    },
    [setBusy],
  );

  const clearActionError = React.useCallback(() => setActionError(null), []);

  const reset = React.useCallback(() => {
    setBusy(false);
    setActionError(null);
    setDraftMessage("");
    setSession(null);
    setResult(null);
    setPlan(null);
    setSelectedHistory(null);
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
    result,
    plan,
    histories,
    selectedHistory,
    extraction,
    problems,
    selectedProblem,
    startConsultation,
    sendDraftMessage,
    organize,
    extract,
    createPlan,
    saveAndGoHistory,
    openHistoryResult,
    openProblemList,
    openProblem,
    triage,
    dismissExtracted,
    createPlanForProblem,
    reset,
  };
}
