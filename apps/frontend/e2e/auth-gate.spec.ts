import type { Page } from "@playwright/test";
import { expect, pageHeading, test } from "./fixtures";

/**
 * L3 — 認証有効ビルド (ダミー VITE_ENTRA_*) での認証ゲートの振る舞い (#112)。
 *
 * ここが無いと静かに通るもの:
 * - **未認証で開いた瞬間に Entra へリダイレクトされ、オンボーディングが表示されない退行**
 *   (#112 の事故クラス: mount 時のデータ読み込みがトークン取得 → loginRedirect を誘発する)。
 *   mock ビルドの e2e は認証ゲート自体をスキップするため、この層はここでしか落とせない。
 *
 * Entra への実通信はしない。login.microsoftonline.com を route で止め、
 * authorize へのナビゲーションはスタブページで受ける (chromium-auth プロジェクト専用)。
 */

/**
 * login.microsoftonline.com への全リクエストを堰き止め、observed に記録する。
 * - metadata 系 (openid-configuration / instance discovery) は最小限の JSON を返す
 *   (msal は公開クラウドではハードコード済みメタデータを使うため、通常は呼ばれない)
 * - それ以外 (authorize 等のページ遷移) はスタブ HTML を返す
 */
async function stubEntra(page: Page, observed: string[]) {
  await page.route("**/*", async (route) => {
    const url = route.request().url();
    if (!url.includes("login.microsoftonline.com")) {
      await route.continue();
      return;
    }

    observed.push(url);

    if (url.includes("openid-configuration") || url.includes("/discovery/instance")) {
      const authority = url.split("/oauth2/")[0];
      await route.fulfill({
        json: {
          token_endpoint: `${authority}/oauth2/v2.0/token`,
          authorization_endpoint: `${authority}/oauth2/v2.0/authorize`,
          issuer: `${authority}/v2.0`,
        },
      });
      return;
    }

    await route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><title>E2E stub Entra</title><p>E2E stub Entra sign-in page</p>",
    });
  });
}

test.describe("認証有効ビルド (未認証)", () => {
  test("[L3] 未認証で開くとオンボーディングが表示され、勝手に Entra へ飛ばない", async ({
    page,
  }) => {
    const entraRequests: string[] = [];
    await stubEntra(page, entraRequests);

    await page.goto("/");

    // オンボーディングが最初に見える画面である (onboarding.mdx / #112)
    await expect(pageHeading(page, "起動画面 / オンボーディング")).toBeVisible();
    await expect(page.getByRole("button", { name: "はじめる" })).toBeVisible();
    await expect(page).toHaveURL(/\/$/);

    // mount 時のデータ読み込み等が Entra への通信 (= loginRedirect) を誘発していないこと。
    // networkidle まで待ってから見ることで「遅れて飛ぶ」パターンも拾う。
    await page.waitForLoadState("networkidle");
    expect(entraRequests, "読み込みだけで Entra へのリクエストが飛んでいる").toEqual([]);

    // PR に貼る用 (ADR 0018)
    await page.screenshot({
      path: "test-results/screens/auth-gate-onboarding.png",
      fullPage: true,
      animations: "disabled",
    });
  });

  test("[L3] はじめる を押した時に初めて Entra サインインへ誘導される", async ({ page }) => {
    const entraRequests: string[] = [];
    await stubEntra(page, entraRequests);

    await page.goto("/");
    await page.getByRole("button", { name: "はじめる" }).click();

    // loginRedirect による authorize へのトップレベル遷移 (スタブが受ける)
    await page.waitForURL(/login\.microsoftonline\.com/);
    expect(
      entraRequests.some((u) => u.includes("/oauth2/v2.0/authorize")),
      "authorize リクエストが飛んでいない",
    ).toBe(true);
  });
});
