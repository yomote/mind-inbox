// @vitest-environment jsdom
/**
 * [L1]/[L2] 環境ステータスバナー (env-status-banner.mdx)。
 *
 * 無いと何が静かに通るか: デプロイ進行中・検証失敗なのにバナーが出ない (または
 * 正常時に常時出る) 実装でも、他のテストは全緑のまま通る。バナーの存在理由は
 * 「PO が触る前に不安定さが分かる」ことなので、状態 → 表示の対応そのものを固定する。
 * あわせて「VITE_ENV_STATUS_REPO 未設定なら外部 fetch を一切しない」を固定する —
 * これが破れると mock デモ・ローカル・CI の E2E が GitHub API に依存し始める。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { deriveEnvStatus, statusPageUrl, useEnvStatus } from "./useEnvStatus";
import { EnvStatusBanner } from "./EnvStatusBanner";

function Harness() {
  const status = useEnvStatus();
  return (
    <div>
      <EnvStatusBanner status={status} />
      <span data-testid="probe">{status}</span>
    </div>
  );
}

function runsResponse(runs: unknown[]) {
  return {
    ok: true,
    json: async () => ({ workflow_runs: runs }),
  } as Response;
}

afterEach(() => {
  // globals 無効設定のため RTL の auto-cleanup は効かない。明示的に DOM を畳む。
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("[L1] deriveEnvStatus", () => {
  it("run が無ければ unknown (バナー非表示側に倒す)", () => {
    expect(deriveEnvStatus([])).toBe("unknown");
  });

  it("進行中の run があれば deploying — 直前が赤でもデプロイ中の案内を優先する", () => {
    expect(
      deriveEnvStatus([
        { status: "in_progress", conclusion: null },
        { status: "completed", conclusion: "failure" },
      ]),
    ).toBe("deploying");
  });

  it("全 run 完了で最新が success なら ok", () => {
    expect(
      deriveEnvStatus([
        { status: "completed", conclusion: "success" },
        { status: "completed", conclusion: "failure" },
      ]),
    ).toBe("ok");
  });

  it("全 run 完了で最新が failure なら broken (cancelled / timed_out も同じ)", () => {
    expect(deriveEnvStatus([{ status: "completed", conclusion: "failure" }])).toBe("broken");
    expect(deriveEnvStatus([{ status: "completed", conclusion: "timed_out" }])).toBe("broken");
  });
});

describe("[L2] useEnvStatus + EnvStatusBanner", () => {
  it("デプロイ進行中なら警告バナーが出る", async () => {
    vi.stubEnv("VITE_ENV_STATUS_REPO", "yomote/mind-inbox");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => runsResponse([{ status: "in_progress", conclusion: null }])),
    );

    render(<Harness />);
    expect(await screen.findByText(/デプロイ進行中です/)).toBeTruthy();
  });

  it("直近のデプロイ検証が失敗なら赤バナー + 状況ページへのリンクが出る", async () => {
    vi.stubEnv("VITE_ENV_STATUS_REPO", "yomote/mind-inbox");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => runsResponse([{ status: "completed", conclusion: "failure" }])),
    );

    render(<Harness />);
    expect(await screen.findByText(/直近のデプロイ検証が失敗しています/)).toBeTruthy();
    const link = screen.getByRole("link", { name: "状況ページ" });
    expect(link.getAttribute("href")).toBe("https://yomote.github.io/mind-inbox/status/");
  });

  it("最新デプロイが成功ならバナーは出ない", async () => {
    vi.stubEnv("VITE_ENV_STATUS_REPO", "yomote/mind-inbox");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => runsResponse([{ status: "completed", conclusion: "success" }])),
    );

    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("ok"));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("VITE_ENV_STATUS_REPO 未設定なら fetch を一切呼ばない (mock / ローカルを外部 API に依存させない)", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("unknown"));
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("fetch が失敗してもクラッシュせずバナー非表示のまま (バナーの障害でアプリを巻き込まない)", async () => {
    vi.stubEnv("VITE_ENV_STATUS_REPO", "yomote/mind-inbox");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );

    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("unknown"));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("[L1] statusPageUrl", () => {
  it("owner/repo から GitHub Pages の状況ページ URL を組み立てる", () => {
    expect(statusPageUrl("yomote/mind-inbox")).toBe("https://yomote.github.io/mind-inbox/status/");
  });
});
