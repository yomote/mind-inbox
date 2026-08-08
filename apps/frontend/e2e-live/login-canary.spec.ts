import { expect, test } from "@playwright/test";

/**
 * [L4] ログイン経路のカナリア — デプロイ済み SWA から Entra のログイン画面まで。
 *
 * 実バンドルは未認証で開くと **読み込み時に自動で Entra へリダイレクト**される
 * (Layout の履歴読み込みがトークン取得を誘発するため。UX 是非は #112 で別議論)。
 * ここではその実挙動どおり「開く → Entra のログイン画面が AADSTS エラーなしで
 * 表示される」ことを検証する。
 *
 * 検知できる実事故クラス: アプリ登録の破壊 (削除・リダイレクト URI 欠落 = AADSTS50011 /
 * SPA 種別違い)、認証無効ビルドの出荷 (VITE_ENTRA_* 空だとリダイレクトが起きない)。
 * 検知できないもの: サインイン後のトークン交換 (SP 不在 AADSTS500011 は認証後に出る)。
 * そこはシナリオテスト (実トークンで EasyAuth を通す) と人間の初回確認が補完する。
 */

const LIVE_APP_URL = process.env.LIVE_APP_URL ?? "";

test.skip(!LIVE_APP_URL, "LIVE_APP_URL が未設定 (実環境向けのみ実行)");

test("[L4] 未認証で開く → Entra ログイン画面がエラーなしで表示される", async ({ page }) => {
  // 自動リダイレクトが起きない = 認証無効ビルド (VITE_ENTRA_* 空) の出荷なので落とす
  await page.goto("/");
  await page.waitForURL(/login\.microsoftonline\.com/, { timeout: 60_000 });

  // AADSTS エラー画面 (アプリ登録破壊系) でなく、サインイン UI が出ていること
  const body = (await page.textContent("body")) ?? "";
  const aadsts = body.match(/AADSTS\d+/);
  expect(aadsts, `Entra がエラーを返した: ${aadsts?.[0] ?? ""}`).toBeNull();
  await expect(page.locator('input[type="email"], input[name="loginfmt"]').first()).toBeVisible({
    timeout: 30_000,
  });
});
