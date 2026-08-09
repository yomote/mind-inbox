import type { Page } from "@playwright/test";
import {
  errorAlert,
  expect,
  extractProblems,
  gotoHome,
  startConsultationAndSay,
  test,
} from "./fixtures";

/**
 * [L3-real] 無音失敗の禁止 (ADR 0018) — **実環境で PO が踏んだ症状の再発防止**。
 *
 * 2026-08-09: dev の SWA で「ボタンを押しても何も起きない」。原因は
 * `useConsultation` の startConsultation 以外のハンドラが例外を握っておらず、
 * 通信が失敗すると unhandled rejection のまま画面が沈黙していたこと。
 *
 * ここで固定するのは 1 つだけ:
 *   **BFF が落ちている状態で操作しても、必ず言葉が出て、嘘の遷移をしない。**
 *
 * mock 版 E2E では原理的に書けない (BFF が居ないので落とせない)。単体テストでも
 * hook までしか見られない。「実配線が落ちたときの画面」はこの層でしか踏めない。
 */

/** BFF への経路をまとめて塞ぐ (tRPC も SSE も落ちた = 通信断・下流障害の再現)。 */
async function breakBff(page: Page) {
  await page.route("**/api/trpc/**", (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ error: { message: "boom", code: -32603 } }),
    }),
  );
  await page.route("**/api/chat/stream", (route) => route.fulfill({ status: 502, body: "boom" }));
}

test.describe("通信が失敗したときに UI が沈黙しない", () => {
  // 意図的に 500 を起こすので、ブラウザの console.error は想定内。
  // (フィクスチャの安全網はここだけ明示的に解除する)
  test.afterEach(async ({ pageErrors }) => {
    pageErrors.length = 0;
  });

  test("[L3-real] 相談を開始できないときは通知が出て、対話画面に飛ばない", async ({ page }) => {
    await gotoHome(page);
    await breakBff(page);

    await page.getByRole("button", { name: "新しい相談を始める" }).click();

    await expect(errorAlert(page)).toContainText("相談を開始できませんでした");
    await expect(page).toHaveURL(/\/home$/);
  });

  test("[L3-real] 送信できないときは通知が出て、入力が消えない", async ({ page }) => {
    // 無いと: 自分の発話だけが残って返事が来ず、打ち直しも強いられる
    await gotoHome(page);
    await startConsultationAndSay(page, "最近やることが多すぎる");

    await breakBff(page);
    const composer = page.getByPlaceholder("ここに入力 / 話して入力");
    await composer.fill("もう少し話したいことがある");
    await page.getByRole("button", { name: "送信" }).click();

    await expect(errorAlert(page)).toContainText("メッセージを送れませんでした");
    await expect(composer).toHaveValue("もう少し話したいことがある");
  });

  test("[L3-real] 抽出できないときは通知が出て、抽出結果画面に飛ばない", async ({ page }) => {
    await gotoHome(page);
    await startConsultationAndSay(page, "やることが多すぎて手が回らない");

    await breakBff(page);
    await page.getByRole("button", { name: "困りごとを抽出" }).click();

    await expect(errorAlert(page)).toContainText("困りごとを抽出できませんでした");
    await expect(page).toHaveURL(/\/consultations\/current$/);
  });

  test("[L3-real] 困りごと一覧を開けないときは通知が出る (押しても無反応にしない)", async ({
    page,
  }) => {
    // これが PO の報告そのもの: ホームでボタンを押しても、遷移もエラーも出なかった
    await gotoHome(page);
    await breakBff(page);

    await page.getByRole("button", { name: "困りごと一覧", exact: true }).click();

    await expect(errorAlert(page)).toContainText("困りごと一覧を開けませんでした");
    await expect(page).toHaveURL(/\/home$/);
  });

  test("[L3-real] トリアージが失敗したときも通知が出る", async ({ page }) => {
    // 無いと: 「解決した」を押しても状態が変わらず、押せていないのか失敗なのか分からない
    await gotoHome(page);
    await startConsultationAndSay(page, "やることが多すぎて優先順位がつけられない");
    await extractProblems(page);
    await page.getByRole("button", { name: "困りごと一覧へ" }).click();
    await page.getByText("やることが多すぎて優先順位をつけられない", { exact: true }).click();
    await expect(page).toHaveURL(/\/problems\/current$/);

    await breakBff(page);
    await page.getByRole("button", { name: "解決した" }).click();

    await expect(errorAlert(page)).toContainText("困りごとを更新できませんでした");
  });

  test("[L3-real] 失敗しても操作不能にならない (復旧したら続けられる)", async ({ page }) => {
    // 無いと: 失敗時に loading が立ちっぱなしになり、以降すべてのボタンが無反応になる
    await gotoHome(page);
    await breakBff(page);
    await page.getByRole("button", { name: "新しい相談を始める" }).click();
    await expect(errorAlert(page)).toBeVisible();

    await page.unroute("**/api/trpc/**");
    await page.unroute("**/api/chat/stream");

    await page.getByRole("button", { name: "新しい相談を始める" }).click();
    await expect(page).toHaveURL(/\/consultations\/current$/);
  });
});

test.describe("状態を持たない画面への直接アクセス", () => {
  test("[L3-real] セッションが無いのに対話 URL を開いたらホームへ戻す", async ({ page }) => {
    // 無いと: session! を触って白画面になる (RouteStateGuard の退行)
    await page.goto("/consultations/current");
    await expect(page).toHaveURL(/\/home$/);
    await expect(page.getByRole("button", { name: "新しい相談を始める" })).toBeVisible();
  });

  test("[L3-real] 選択していないのに困りごと詳細 URL を開いたら一覧へ戻す", async ({ page }) => {
    await page.goto("/problems/current");
    await expect(page).toHaveURL(/\/problems$/);
  });
});
