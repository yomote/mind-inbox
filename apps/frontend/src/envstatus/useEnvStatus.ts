import * as React from "react";

/**
 * 「今この環境、触って大丈夫?」の判定 (env-status-banner.mdx)。
 *
 * dev は main への push ごとに自動デプロイされ (ADR 0013)、デプロイ直後の数分は
 * BFF の再起動で不安定になる (#118)。PO が触る場所 = アプリ自身に状態を出すため、
 * 公開リポジトリの GitHub Actions API を匿名で読み、deploy workflow の状態を導出する。
 *
 * VITE_ENV_STATUS_REPO 未設定 (ローカル / mock / テスト) では一切 fetch しない —
 * 外部サービス無しでも動く特性 (CLAUDE.md の stub fallback) を壊さないため。
 */

export type EnvStatus = "ok" | "deploying" | "broken" | "unknown";

export type WorkflowRunLike = {
  status: string; // queued | in_progress | completed
  conclusion: string | null; // success | failure | cancelled | timed_out | ...
};

// 匿名 GitHub API の rate limit は 60 req/h/IP。ポーリングはその半分以下に抑える。
const POLL_INTERVAL_MS = 120_000;

export function statusPageUrl(repo: string): string {
  const [owner, name] = repo.split("/");
  return `https://${owner}.github.io/${name}/status/`;
}

export function deriveEnvStatus(runs: WorkflowRunLike[]): EnvStatus {
  if (runs.length === 0) return "unknown";
  // 進行中が 1 本でもあれば「デプロイ中」を優先する。直前が赤でも、いま走っている
  // deploy が直すかもしれないので、赤バナーより「数分待って」の案内が正しい。
  if (runs.some((run) => run.status !== "completed")) return "deploying";
  return runs[0].conclusion === "success" ? "ok" : "broken";
}

export function useEnvStatus(): EnvStatus {
  const repo = import.meta.env.VITE_ENV_STATUS_REPO;
  const [status, setStatus] = React.useState<EnvStatus>("unknown");

  React.useEffect(() => {
    if (!repo) return;
    let active = true;

    const refresh = async () => {
      try {
        const res = await fetch(
          `https://api.github.com/repos/${repo}/actions/workflows/deploy.yml/runs?branch=main&per_page=5`,
          { headers: { Accept: "application/vnd.github+json" } },
        );
        // rate limit や一時的な 5xx。判定不能でアプリ本体を巻き込まず、前回の表示を保つ。
        if (!res.ok || !active) return;
        const body = (await res.json()) as { workflow_runs?: WorkflowRunLike[] };
        if (!active) return;
        setStatus(deriveEnvStatus(body.workflow_runs ?? []));
      } catch {
        // ネットワーク断でも同上。バナーは死活監視の代替であり、失敗が UX を壊してはいけない。
      }
    };

    void refresh();
    const timer = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    // 「ぱっと触るとき」= タブに戻った瞬間が一番知りたい瞬間なので、その場で最新化する。
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      active = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [repo]);

  return status;
}
