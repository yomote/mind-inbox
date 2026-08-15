import { expect, gotoHome, pageHeading, test } from "./fixtures";

/**
 * L3 — 主要画面のスクリーンショットを撮る (ADR 0018 の「UI 変更はローカルで見る」)。
 *
 * 出力先は `apps/frontend/test-results/screens/` (git 管理外)。PR に貼る用。
 * 画像を commit して比較する visual regression は **やらない** — レンダリング差分で
 * 落ちる保守コストに対して、個人開発では見合わないため。ここでの assert は
 * 「その画面が実際に描画されたか」だけで、画像は人が見るための副産物。
 */

// animations: "disabled" が無いと MUI の遷移途中 (メニューが閉じきる前 / Switch が
// 動いている最中) が写り、同じ状態でも毎回違う絵になる。
const shot = (name: string) =>
  ({
    path: `test-results/screens/${name}.png`,
    fullPage: true,
    animations: "disabled",
  }) as const;

test.describe("主要画面のスクリーンショット", () => {
  test("[L3] ホーム / 対話 / 抽出結果 / 困りごと一覧 を撮る", async ({ page }) => {
    await gotoHome(page);
    await expect(pageHeading(page, "ホーム")).toBeVisible();
    await page.screenshot(shot("01-home"));

    await page.getByRole("button", { name: "新しい相談を始める" }).click();
    await page.getByPlaceholder("ここに入力 / 話して入力").fill("週末になると気分が落ち込む");
    await page.getByRole("button", { name: "送信" }).click();
    await expect(page.getByText("週末になると気分が落ち込む").first()).toBeVisible();
    await page.screenshot(shot("02-session"));

    // mock ビルドは下書きプレビュー有効 (#187 / ADR 0039)。確定対象の下書きを
    // 「今すぐ整理」で作ってから「この内容で確定」する (表示中の下書きの保存)。
    await page.getByRole("button", { name: "今すぐ整理" }).click();
    // 右ペインの既定タブは「AI の整理」(#433)。下書きカードは「下書き」タブ側にある。
    await page.getByTestId("draft-tab").click();
    await expect(page.getByTestId("preview-card").first()).toBeVisible();
    await page.getByRole("button", { name: "この内容で確定" }).click();
    await expect(page.getByText(/件の困りごとを見つけました/)).toBeVisible();
    await page.screenshot(shot("03-extract-review"));

    await page.getByRole("button", { name: "困りごと一覧へ" }).click();
    await expect(pageHeading(page, "困りごと一覧")).toBeVisible();
    await page.screenshot(shot("04-problem-list"));
  });

  test("[L3] 設定画面を撮る (ダークテーマ切替を含む)", async ({ page }) => {
    await gotoHome(page);

    await page.getByRole("button", { name: "アカウント" }).click();
    await page.getByRole("menuitem", { name: "設定" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(pageHeading(page, "設定")).toBeVisible();
    await page.screenshot(shot("05-settings-light"));

    await page.getByLabel("ダークテーマ").click();
    await page.screenshot(shot("06-settings-dark"));
  });
});
