import { expect, gotoHome, startConsultationAndSay, test } from "./fixtures";

/**
 * [スモーク] 副作用ツールの承認ゲート (G1 / ADR 0016 M1-3 / dialogue-session.mdx §5.9)。
 *
 * 事後条件: 承認要求が**画面に出て**、承認 / 却下がサーバまで届き、その結果が会話に残る。
 *
 * 無いと何が静かに通るか: `requires_approval` はサーバ → BFF → フロントの api 層まで
 * 運ばれているのに、**どの画面もそれを読まない**状態 (#82 の着手前がこれ) が緑のまま通る。
 * 単体テストは api 層と hook を別々に見るだけなので、tRPC の `consultation.approve` が
 * 未結線でも、Router / Layout が承認要求を SessionScreen に渡し忘れていても気づけない。
 * この spec は実フロント → 実 BFF → ai-agent ダブルの経路で押す。
 */

// 承認を要求させる発話。TOPICS のキーワードをどれも含まないので Problem を作らない
// (BFF の Problem リポジトリは全 spec 共有の in-memory — 他 spec の件数を動かさない)。
const UTTERANCE = "この件、返信しておいて";
const REQUEST_TEXT = "「send_reply」を実行するには承認が必要です。実行してよろしいですか？";

test.describe("承認ゲート — 副作用ツールは人間が承認するまで実行しない", () => {
  test("[スモーク] 承認すると実行結果が会話に返り、承認カードが閉じる", async ({ page }) => {
    await gotoHome(page);
    await startConsultationAndSay(page, UTTERANCE);

    // カードは単体で「何を実行しようとしているか」が読めること (§5.9)
    const card = page.getByTestId("approval-request");
    await expect(card).toBeVisible();
    await expect(card).toContainText("send_reply");
    await expect(card).toContainText(REQUEST_TEXT);

    await page.getByRole("button", { name: "承認して実行" }).click();

    // 実行された結果が会話に残る (押した結果が画面に出ない = 何が起きたか分からない)
    await expect(page.getByText("[stub] Reply sent to team@example.com.")).toBeVisible();
    await expect(card).toBeHidden();
  });

  test("[スモーク] 却下するとキャンセルされ、承認カードが閉じる", async ({ page }) => {
    await gotoHome(page);
    await startConsultationAndSay(page, UTTERANCE);

    const card = page.getByTestId("approval-request");
    await expect(card).toBeVisible();

    await page.getByRole("button", { name: "却下する" }).click();

    // 却下も**サーバまで届く** — 届いていなければ ai-agent ダブルは承認待ちのままで、
    // このキャンセル応答は返らない (画面から消すだけの実装をここで落とす)。
    await expect(
      page.getByText("操作はキャンセルされました。他にご用件はありますか？"),
    ).toBeVisible();
    await expect(card).toBeHidden();
  });
});
