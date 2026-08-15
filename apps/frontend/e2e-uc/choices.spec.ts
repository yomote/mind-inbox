import { expect, gotoHome, startConsultationAndSay, test } from "./fixtures";

/**
 * [スモーク] AI が提示する選択肢 (#432-b / dialogue-session.mdx §5.10)。
 *
 * 事後条件: 選択肢が**画面に出て**、タップした文言が**ユーザーの発話として**サーバまで
 * 届き、その返事が会話に残る。押さずに自由記述で進むこともできる。
 *
 * 無いと何が静かに通るか: `choices` は ai-agent → BFF → フロントの api 層まで運ばれて
 * いるのに、**どの画面もそれを読まない**状態が緑のまま通る (#82 の着手前と同じ形)。
 * 単体テストは api 層 / hook / コンポーネントを別々に見るだけなので、Router や Layout が
 * `offeredChoices` を SessionScreen に渡し忘れていても気づけない。この spec は
 * 実フロント → 実 BFF → ai-agent ダブルの経路で押す。
 *
 * **承認 (approval-gate.spec.ts) との違いをここで踏む**: 選択肢はサーバに待ち状態を
 * 残さない (完了型 / PO 裁定 2026-08-15) ので、押さずに次を送っても却下相当の
 * リクエストは存在しない。
 */

// 選択肢を出させる発話。TOPICS のキーワードをどれも含まないので Problem を作らない
// (BFF の Problem リポジトリは全 spec 共有の in-memory — 他 spec の件数を動かさない)。
const UTTERANCE = "なんだかもやもやするけど、わからないんです";
const CHOICE = "家族やパートナーのこと";

test.describe("選択肢 — AI が差し出した選択肢を押すと、その文言が次の発話になる", () => {
  test("[スモーク] タップした文言が発話として会話に残り、返事が返る", async ({ page }) => {
    await gotoHome(page);
    await startConsultationAndSay(page, UTTERANCE);

    const options = page.getByTestId("choice-options");
    await expect(options).toBeVisible();

    // 選んでいる間も入力欄は塞がない (§5.10 / 混合が裁定)
    await expect(page.getByPlaceholder("ここに入力 / 話して入力")).toBeEnabled();
    // 承認カードとは別物 — 選択肢のターンで承認カードは出ない
    await expect(page.getByTestId("approval-request")).toHaveCount(0);

    const guide = page.getByText("ガイド", { exact: true });
    const repliesBefore = await guide.count();

    await page.getByRole("button", { name: CHOICE }).click();

    // 押した文言が**ユーザーの発話として**会話に残る (AI が決めたことにしない)
    await expect(page.getByText(CHOICE, { exact: true }).first()).toBeVisible();
    // サーバまで届いている = 返事が 1 つ増える (画面上でチップを消すだけの実装を落とす)
    await expect(guide).toHaveCount(repliesBefore + 1);
    // 答え済みの分岐は残さない
    await expect(options).toBeHidden();
  });

  test("[スモーク] 押さずに自由記述で答えても会話が進む (選択肢は消える)", async ({ page }) => {
    await gotoHome(page);
    await startConsultationAndSay(page, UTTERANCE);

    const options = page.getByTestId("choice-options");
    await expect(options).toBeVisible();

    const guide = page.getByText("ガイド", { exact: true });
    const repliesBefore = await guide.count();
    const composer = page.getByPlaceholder("ここに入力 / 話して入力");

    // 承認 (§5.9) と違い、押さなかったことをサーバへ知らせる必要は無い。選択肢の
    // 「却下」を送る実装だと ai-agent ダブルにその口が無く、通信エラーで落ちる
    // (fixtures が console.error を失敗として拾う)
    await composer.fill("自分で書きます");
    await page.getByRole("button", { name: "送信" }).click();

    await expect(page.getByText("自分で書きます", { exact: true }).first()).toBeVisible();
    await expect(guide).toHaveCount(repliesBefore + 1);
    await expect(options).toBeHidden();
  });
});
