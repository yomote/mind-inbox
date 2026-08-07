import { test as base, expect, type Page } from "@playwright/test";

/**
 * 「JS が死んでいるのに画面だけ出ている」を落とすための共通装備 (ADR 0018)。
 *
 * mock ビルドは BFF も認証も無いので、壊れ方はほぼ **バンドル読み込み時の例外**
 * (import 時に window を触る / 未定義の env を参照する) になる。React は例外を
 * 握って空の div を残すことがあり、「表示は白いが 200 は返る」状態を作る。
 * そのため全テストで pageerror と console.error を回収し、テスト終了時に落とす。
 */
export const test = base.extend<{ pageErrors: string[] }>({
  pageErrors: async ({ page }, use) => {
    const errors: string[] = [];

    page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
    });

    await use(errors);

    expect(errors, "ブラウザ側で未処理のエラーが出ている").toEqual([]);
  },
});

export { expect };

/**
 * mock ビルドのホーム画面まで進める。
 *
 * `VITE_USE_MOCK=true` の production ビルドでは認証ゲートが無いため、
 * "/" は onboarding ではなく /home へリダイレクトされる (Layout.tsx / Router.tsx)。
 */
/**
 * 画面上部の見出し (Layout.tsx が出す h5)。
 *
 * 画面内にも同じ語の見出しが出ることがある (例: 設定画面の h6「設定」) ので、
 * レベルで絞って「今どの画面にいるか」だけを見る。
 */
export function pageHeading(page: Page, name: string) {
  return page.getByRole("heading", { level: 5, name, exact: true });
}

export async function gotoHome(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "新しい相談を始める" })).toBeVisible();
}
