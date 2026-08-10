import { expect, gotoHome, pageHeading, test } from "./fixtures";

/**
 * L3 — mock ビルド (認証なし) をブラウザで通す (ADR 0018)。
 *
 * ここが無いと静かに通るもの:
 * - 認証ゲートの判定が壊れて mock デモが「認証状態を確認中...」のまま止まる
 *   (vitest は Layout の分岐を単体では見るが、production バンドルの実挙動は見ない)
 * - production バンドルでだけ落ちる初期化 (import 時の window 参照など)
 * - 画面遷移ガード (ProtectedRoute / RouteStateGuard) の退行でホームに弾き返される
 */

test.describe("mock ビルド (認証なし)", () => {
  test("[L3] 認証を挟まずホームに到達し、認証待ちの表示が残らない", async ({ page }) => {
    await gotoHome(page);

    // 認可の門は Functions(EasyAuth) 側にあり、mock ビルドには門が無い (#69)。
    // ログイン導線が出ていたら認証状態の判定が壊れている。
    await expect(page).toHaveURL(/\/home$/);
    await expect(page.getByText("認証状態を確認中...")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "はじめる" })).toHaveCount(0);

    await expect(pageHeading(page, "ホーム")).toBeVisible();
    await expect(page.getByRole("button", { name: "困りごと一覧" })).toBeVisible();
  });

  test("[L3] 相談 → 発話 → 困りごと抽出 → 一覧まで通る", async ({ page }) => {
    await gotoHome(page);

    // UI 仕様 (home.mdx): テーマ入力画面は挟まず、直接対話セッションが始まる
    // (タイトルは最初の発話から自動生成 — newConsultation 画面は 2026-08-07 に廃止)
    await page.getByRole("button", { name: "新しい相談を始める" }).click();
    await expect(page).toHaveURL(/\/consultations\/current$/);

    const composer = page.getByPlaceholder("ここに入力 / 話して入力");
    await composer.fill("最近やることが多すぎて優先順位がつけられない");
    await page.getByRole("button", { name: "送信" }).click();

    // mock の AI 応答が返ってセッションに積まれる (発話が消えない = 送信後の state 更新)。
    await expect(
      page.getByText("最近やることが多すぎて優先順位がつけられない").first(),
    ).toBeVisible();
    await expect(composer).toHaveValue("");

    await page.getByRole("button", { name: "困りごとを抽出" }).click();
    await expect(page).toHaveURL(/\/consultations\/current\/extract$/);
    await expect(page.getByText(/件の困りごとを見つけました/)).toBeVisible();

    await page.getByRole("button", { name: "困りごと一覧へ" }).click();
    await expect(page).toHaveURL(/\/problems$/);
    await expect(pageHeading(page, "困りごと一覧")).toBeVisible();
    // 抽出したものが一覧に載っている (載っていなければ抽出→一覧の接続が切れている)。
    await expect(page.getByText("該当する困りごとはありません。")).toHaveCount(0);
  });

  test("[L3] 状態を持たない画面へ直接入るとホームへ戻される", async ({ page }) => {
    // セッションが無いのに対話画面の URL を直接開く。RouteStateGuard が効いていないと
    // session! を触って白画面になる (props が non-null assertion のため)。
    await page.goto("/consultations/current");
    await expect(page).toHaveURL(/\/home$/);
    await expect(page.getByRole("button", { name: "新しい相談を始める" })).toBeVisible();
  });

  test("[L3] 困りごと一覧 → 詳細 → 一覧に戻れる", async ({ page }) => {
    await gotoHome(page);

    await page.getByRole("button", { name: "困りごと一覧" }).click();
    await expect(page).toHaveURL(/\/problems$/);

    // mock の既存データを 1 件開く (一覧が空なら fixture 側の退行)。
    // カードは Paper に onClick を付けただけで role=button ではないため (issue で別途)、
    // 各カードが必ず持つ「最終言及 …」のキャプションを起点にクリックする。
    const firstProblem = page.getByText(/^最終言及 /).first();
    await expect(firstProblem).toBeVisible();
    await firstProblem.click();
    await expect(page).toHaveURL(/\/problems\/current$/);
    await expect(pageHeading(page, "困りごと詳細")).toBeVisible();

    // 戻り道も踏む。ここが無いと「戻るボタンが消えた / 壊れた」まま緑で通る。
    await page.getByRole("button", { name: "一覧へ" }).click();
    await expect(page).toHaveURL(/\/problems$/);
    await expect(pageHeading(page, "困りごと一覧")).toBeVisible();
  });

  test("[L3] 旧 PoC 導線 (整理結果 / 履歴) が UI から消えている", async ({ page }) => {
    // 無いと: ADR 0034 で撤去した導線が復活しても誰も気づかない。
    // 「整理結果へ」は下流 (/organize) が 404 を返す壊れた導線だったので、
    // 押せる状態に戻してはいけない。
    await gotoHome(page);
    await expect(page.getByRole("button", { name: "履歴・振り返り" })).toHaveCount(0);

    await page.getByRole("button", { name: "新しい相談を始める" }).click();
    await expect(page.getByRole("button", { name: "整理結果へ" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "困りごとを抽出" })).toBeVisible();
  });
});
