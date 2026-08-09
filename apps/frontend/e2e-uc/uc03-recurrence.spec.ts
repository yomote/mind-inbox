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
 * [L3-real] UC-03 繰り返している悩みに気づく (docs/design/use_cases.md UC-03)。
 *
 * concept_deck §1 が直接解決すると謳っている中核。**別セッションで同じ悩みを話したら、
 * 新しい困りごとを増やすのではなく既存に束ね、「N 回目」と気づかせる**。
 *
 * 無いと静かに通るもの: グルーピング (段2) が壊れて毎回新規 Problem が増える状態。
 * 画面は正常に描画され、一覧も一見それらしいので、数えない限り気づけない。
 * これが死ぬと Mind Inbox は「流れて消えるチャット」に戻る。
 */

const TOPIC = {
  first: "最近ぜんぜん眠れない",
  second: "また眠れない日が続いてる",
  title: "眠りが浅くて疲れが取れない",
};

test.describe.serial("UC-03 繰り返している悩みに気づく", () => {
  test("[L3-real] 別セッションで同じ悩みを話すと既存に束ねられ「2回目」と気づかせる", async ({
    page,
  }) => {
    await gotoHome(page);

    // 1 回目 — 新規 Problem が立つ
    await startConsultationAndSay(page, TOPIC.first);
    await extractProblems(page);
    await expect(page.getByText("新規 1 件 / 既存に追加 0 件")).toBeVisible();

    // 2 回目 — **別セッション**で同じ悩みを話す
    await gotoHome(page);
    await startConsultationAndSay(page, TOPIC.second);
    await extractProblems(page);

    // 3〜4. 再出現が記録され、気づきとして提示される
    await expect(page.getByText("新規 0 件 / 既存に追加 1 件")).toBeVisible();
    await expect(page.getByText("既存に追加", { exact: true })).toBeVisible();
    await expect(page.getByText("🔁 2回目")).toBeVisible();
    await expect(page.getByText("この困りごとは繰り返し話しています")).toBeVisible();
  });

  test("[L3-real] 一覧に回数バッジが出て、詳細に 2 件の言及が時系列で残る", async ({ page }) => {
    // 無いと: 「繰り返している」ことが一覧からは分からず、詳細でも履歴が上書きされて消える
    await gotoHome(page);
    await openProblemList(page);

    await expect(page.getByText("🔁 2回")).toBeVisible();

    await openProblem(page, TOPIC.title);
    await expect(page.getByText("言及の履歴（2件）")).toBeVisible();
    await expect(page.getByText("言及 2 回")).toBeVisible();
    // Mention は不変・追記専用 (ADR 0007) — 1 回目の発話が残っていること
    await expect(page.getByText(`「${TOPIC.first}」`)).toBeVisible();
    await expect(page.getByText(`「${TOPIC.second}」`)).toBeVisible();
  });

  test("[L3-real] よく出る順に並べ替えると、繰り返しているものが先頭に来る", async ({ page }) => {
    // 無いと: 「何度も話していること」が埋もれる (継続テーマに気づく導線が死ぬ)
    await gotoHome(page);
    await openProblemList(page);

    await page.getByRole("button", { name: "よく出る順" }).click();

    const firstCard = page.getByRole("button", { name: /・言及 \d+ 回）$/ }).first();
    await expect(firstCard).toHaveAccessibleName(new RegExp(TOPIC.title));
  });

  test("[L3-real] 一覧のカードはキーボードだけで開ける (#98)", async ({ page }) => {
    // 無いと: マウスでしか開けない一覧のまま (Paper + onClick は button ではない)
    await gotoHome(page);
    await openProblemList(page);

    const card = page.getByRole("button", { name: new RegExp(TOPIC.title) });
    await card.focus();
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/problems\/current$/);
  });
});
