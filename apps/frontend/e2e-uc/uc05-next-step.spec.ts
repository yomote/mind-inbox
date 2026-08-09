import {
  expect,
  extractProblems,
  gotoHome,
  openProblem,
  openProblemList,
  startConsultationAndSay,
  test,
} from "./fixtures";

/**
 * [L3-real] UC-05 困りごとから次の一歩を作る (docs/design/use_cases.md UC-05)。
 *
 * 事後条件: ActionPlan が生成されて Problem に紐づく。**Problem の状態は変わらない**
 * (プランは派生物 / ADR 0007)。
 * 代替 0a: 望まれない限りプランを出さない — **ユーザー起点でのみ**発火する
 * (concept_deck §4「望まれたときだけ次の一歩」)。
 *
 * 無いと静かに通るもの: BFF の `problem.createPlan` が壊れてもボタンは押せるので、
 * 「押したのに何も増えない」状態が実環境でだけ起きる。
 */

const TOPIC = {
  utterance: "このままでいいのか分からなくなってきた",
  title: "このままでいいのか分からない",
};

test.describe.serial("UC-05 困りごとから次の一歩を作る", () => {
  test.beforeEach(async ({ page }) => {
    await gotoHome(page);
  });

  test("[L3-real] 頼まれるまでプランを出さない (代替フロー 0a)", async ({ page }) => {
    // 無いと: 吐き出しただけで勝手に「次の一歩」を突きつけるアプリになる
    await startConsultationAndSay(page, TOPIC.utterance);
    await extractProblems(page);

    await expect(page.getByText("次の一歩", { exact: true })).toHaveCount(0);

    await page.getByRole("button", { name: "困りごと一覧へ" }).click();
    await openProblem(page, TOPIC.title);
    // 詳細に来ても、押すまでは提案しない (ボタンだけがある)
    await expect(page.getByRole("button", { name: "次の一歩を作る" })).toBeVisible();
    await expect(page.getByText("次の一歩", { exact: true })).toHaveCount(0);
  });

  test("[L3-real] ユーザーが頼んだらプランが生成され、Problem に紐づく", async ({ page }) => {
    await openProblemList(page);
    await openProblem(page, TOPIC.title);

    await page.getByRole("button", { name: "次の一歩を作る" }).click();

    // 3. プランを提示する
    await expect(page.getByText("次の一歩", { exact: true })).toBeVisible();
    await expect(page.getByText("いちばん小さい一歩を1つ選ぶ")).toBeVisible();

    // 5. Problem に紐づく / **状態は変えない** (派生物)
    await expect(page.getByText("追跡中", { exact: true })).toBeVisible();

    // 一覧から開き直しても残っている (画面ローカルの飾りになっていないこと)
    await page.getByRole("button", { name: "一覧へ" }).click();
    await openProblem(page, TOPIC.title);
    await expect(page.getByText("いちばん小さい一歩を1つ選ぶ")).toBeVisible();
  });
});
