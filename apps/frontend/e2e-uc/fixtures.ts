import { test as base, expect, type APIRequestContext, type Page } from "@playwright/test";

/**
 * UC 受け入れテストの共通装備。
 *
 * mock 版 (e2e/fixtures.ts) と同じく「JS が死んでいるのに画面だけ出ている」を落とすが、
 * こちらは BFF まで繋がるので **通信起因のエラーもここで拾える**。
 * 意図的に失敗を起こす spec (failure-modes) は `pageErrors` を明示的に空にして抜ける。
 */
export const test = base.extend<{ pageErrors: string[] }>({
  pageErrors: [
    async ({ page }, use) => {
      const errors: string[] = [];

      page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
      page.on("console", (msg) => {
        if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
      });

      await use(errors);

      expect(errors, "ブラウザ側で未処理のエラーが出ている").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

/** 画面上部の見出し (Layout.tsx が出す h5) = 「今どの画面にいるか」。 */
export function pageHeading(page: Page, name: string) {
  return page.getByRole("heading", { level: 5, name, exact: true });
}

/**
 * ホームまで進める。
 *
 * このビルドは `VITE_ENTRA_*` を焼き込んでいないので認証ゲートが開き、
 * "/" は /home へリダイレクトされる (Layout.tsx)。ユースケースの検証に集中するための構成で、
 * 認証そのものは e2e/auth-gate.spec.ts (L3) と login-canary (L4) が担当する。
 */
export async function gotoHome(page: Page) {
  await page.goto("/");
  await expect(page).toHaveURL(/\/home$/);
  await expect(page.getByRole("button", { name: "新しい相談を始める" })).toBeVisible();
}

/**
 * UC-01 の基本フロー 1〜2: 相談を開始して吐き出す (Dump)。
 *
 * 返事は ai-agent ダブルから SSE で逐次届く。**返事が画面に出るまで待つ**のが重要で、
 * ここを待たずに次へ進むと「返事が来ない」退行がテストをすり抜ける。
 */
export async function startConsultationAndSay(page: Page, ...utterances: string[]) {
  await page.getByRole("button", { name: "新しい相談を始める" }).click();
  await expect(page).toHaveURL(/\/consultations\/current$/);

  const guide = page.getByText("ガイド", { exact: true });
  // 開始直後は AI の挨拶を出さない (#241 / dialogue-session.mdx §3.1)。会話は空で始まり、
  // 待機マスコットだけが表示される。ここが 0 でないなら撤去した挨拶が復活している。
  await expect(guide).toHaveCount(0);
  await expect(page.getByTestId("mascot-empty-state")).toBeVisible();

  const composer = page.getByPlaceholder("ここに入力 / 話して入力");

  for (const utterance of utterances) {
    const repliesBefore = await guide.count();
    await composer.fill(utterance);
    await page.getByRole("button", { name: "送信" }).click();

    // 自分の発話が残っていること (送信後の state 更新) と、AI の返事が 1 つ増えること。
    // `.first()` が要る: 最初の発話はセッションのタイトルにも使われるため
    // (deriveSessionTitle)、短い発話だと見出しと吹き出しの 2 箇所に出る。
    await expect(page.getByText(utterance, { exact: true }).first()).toBeVisible();
    await expect(guide).toHaveCount(repliesBefore + 1);
    await expect(composer).toHaveValue("");
  }
}

/** UC-01 の基本フロー 2〜4: 抽出を走らせて結果レビューに着く。 */
export async function extractProblems(page: Page) {
  await page.getByRole("button", { name: "困りごとを抽出" }).click();
  await expect(page).toHaveURL(/\/consultations\/current\/extract$/);
}

/**
 * UC-02 の基本フロー 1: ホームから困りごと一覧を開く。
 *
 * `exact` が要る: 既定の name 照合は部分一致なので、抽出結果レビューの
 * 「困りごと一覧**へ**」まで拾ってしまい、どの画面から来たのか分からなくなる。
 */
export async function openProblemList(page: Page) {
  await page.getByRole("button", { name: "困りごと一覧", exact: true }).click();
  await expect(page).toHaveURL(/\/problems$/);
  await expect(pageHeading(page, "困りごと一覧")).toBeVisible();
}

/**
 * 一覧から特定の困りごとを開く。
 *
 * カードは Paper + onClick で role=button を持たないため (#98)、タイトル文字列から
 * カード本体へ遡ってクリックする。#98 が直ったらここは getByRole に置き換える。
 */
export async function openProblem(page: Page, title: string) {
  await page.getByText(title, { exact: true }).click();
  await expect(page).toHaveURL(/\/problems\/current$/);
  await expect(pageHeading(page, "困りごと詳細")).toBeVisible();
}

/**
 * 「棚卸し済みも表示」トグルを入れる。
 *
 * MUI の Switch は透明な input が実体で `check()` が届かないことがあるため、
 * ラベルをクリックして切り替える (screens.spec.ts のダークテーマ切替と同じ流儀)。
 */
export async function showArchivedProblems(page: Page) {
  await page.getByLabel("棚卸し済みも表示").click();
}

/** 画面に出ている失敗通知 (Snackbar / Alert)。 */
export function errorAlert(page: Page) {
  return page.getByRole("alert");
}

/**
 * ai-agent 側のセッション履歴だけを消す (#183)。
 * scale-to-zero でレプリカが落ちた / スケールアウトで別レプリカに当たった状況を作る。
 */
export async function dropAiAgentSessions(request: APIRequestContext) {
  const port = process.env.E2E_UC_AI_AGENT_PORT || "8099";
  const res = await request.post(`http://127.0.0.1:${port}/__drop-sessions`);
  if (!res.ok()) throw new Error(`__drop-sessions failed: ${res.status()}`);
}
