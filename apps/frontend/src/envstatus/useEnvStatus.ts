import * as React from "react";

/**
 * 「今この環境、触って大丈夫?」の判定 (env-status-banner.mdx)。
 *
 * dev は main への push ごとに自動デプロイされ (ADR 0013)、デプロイ直後の数分は
 * BFF の再起動で不安定になる (#118)。PO が触る場所 = アプリ自身に状態を出すため、
 * 公開リポジトリの GitHub Actions API を匿名で読み、deploy workflow の状態を導出する。
 *
 * VITE_ENV_STATUS_REPO 未設定 (ローカル / mock / テスト) では一切 fetch しない —
 * 外部サービス無しでも動く特性 (apps/bff/CLAUDE.md「stub fallback を壊さない」節) を壊さないため。
 *
 * 匿名 API の rate limit は 60 req/h/IP で、複数タブで共有される。超えないための 3 段:
 * 前面タブしか fetch しない / 結果を localStorage で全タブ共有 (TTL 内は fetch しない) /
 * ポーリングは 180 秒 (20 req/h/タブ)。前面タブ 2 枚が最悪重なっても 40 req/h。
 */

export type EnvStatus = "ok" | "deploying" | "broken" | "unknown";

export type WorkflowRunLike = {
  status: string; // queued | in_progress | completed
  conclusion: string | null; // success | failure | cancelled | timed_out | ...
};

const POLL_INTERVAL_MS = 180_000;
const CACHE_KEY = "mind-inbox:env-status";
// TTL はポーリング間隔より短くする (自タブの次回 tick では必ず取り直す) が、
// 他タブが直近に取った結果には乗れる程度に長くする。
const CACHE_TTL_MS = 150_000;

type CacheRecord = { ts: number; status: EnvStatus };

function readCache(): CacheRecord | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CacheRecord;
    if (typeof parsed?.ts !== "number" || typeof parsed?.status !== "string") return null;
    return parsed;
  } catch {
    return null;
  }
}

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
    // 遅れて返ってきた古いレスポンスが新しい判定を巻き戻さないよう、最新の要求だけを採用する。
    let seq = 0;

    const refresh = async () => {
      // 背面タブは quota を前面タブに譲る (戻った瞬間の visibilitychange で追いつく)。
      if (document.visibilityState === "hidden") return;
      const cached = readCache();
      if (cached && Date.now() - cached.ts < CACHE_TTL_MS) {
        setStatus(cached.status);
        return;
      }
      const mySeq = ++seq;
      try {
        const res = await fetch(
          `https://api.github.com/repos/${repo}/actions/workflows/deploy.yml/runs?branch=main&per_page=5`,
          { headers: { Accept: "application/vnd.github+json" } },
        );
        // rate limit や一時的な 5xx。判定不能でアプリ本体を巻き込まず、前回の表示を保つ。
        if (!res.ok || !active) return;
        const body = (await res.json()) as { workflow_runs?: WorkflowRunLike[] };
        if (!active || mySeq !== seq) return;
        const next = deriveEnvStatus(body.workflow_runs ?? []);
        setStatus(next);
        try {
          localStorage.setItem(CACHE_KEY, JSON.stringify({ ts: Date.now(), status: next }));
        } catch {
          // ストレージが使えなくても fetch 結果自体は表示できている
        }
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
    // 別タブが取った結果をその場で反映する (storage イベントは書いたタブ以外に飛ぶ)。
    const onStorage = (event: StorageEvent) => {
      if (event.key !== CACHE_KEY) return;
      const cached = readCache();
      if (cached) setStatus(cached.status);
    };
    window.addEventListener("storage", onStorage);
    return () => {
      active = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("storage", onStorage);
    };
  }, [repo]);

  return status;
}
