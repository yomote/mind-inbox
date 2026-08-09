import {
  expect,
  extractProblems,
  gotoHome,
  openProblem,
  openProblemList,
  showArchivedProblems,
  startConsultationAndSay,
  test,
} from "./fixtures";

/**
 * [L3-real] UC-04 困りごとを棚卸しする (docs/design/use_cases.md UC-04)。
 *
 * 事後条件: Problem が `resolved` / `shelved` に遷移し、既定の一覧から外れる。
 * 代替 1a: 後から掘り起こして `open` に戻せる。
 *
 * さらに UC-03 との接続 — **棚卸し済みの困りごとをまた話したら追跡に戻す (再燃)**。
 * ここが繋がっていないと、抽出結果レビューは「再燃」と表示するのに一覧には出てこない、
 * という食い違いが起きる (2026-08-09 に発見。BFF 側は再点火していなかった)。
 */

const TOPIC = {
  first: "お金の先行きが不安で仕方ない",
  again: "また家計のことを考えると不安になる",
  title: "お金の先行きが不安",
};

test.describe.serial("UC-04 困りごとを棚卸しする", () => {
  test("[L3-real] 解決 → 既定の一覧から外れる / 再オープンで戻る", async ({ page }) => {
    await gotoHome(page);
    await startConsultationAndSay(page, TOPIC.first);
    await extractProblems(page);
    await gotoHome(page);
    await openProblemList(page);

    // 1〜3. Problem を選んで「解決した」→ 状態遷移 → 既定の一覧から外す
    await openProblem(page, TOPIC.title);
    await page.getByRole("button", { name: "解決した" }).click();
    await expect(page.getByText("解決", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "また気になる（再オープン）" })).toBeVisible();

    await page.getByRole("button", { name: "一覧へ" }).click();
    await expect(page.getByText(TOPIC.title, { exact: true })).toHaveCount(0);

    // 代替 1a. 掘り起こす → open に戻る
    await showArchivedProblems(page);
    await openProblem(page, TOPIC.title);
    await page.getByRole("button", { name: "また気になる（再オープン）" }).click();
    await expect(page.getByText("追跡中", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "一覧へ" }).click();
    await expect(page.getByText(TOPIC.title, { exact: true })).toBeVisible();
  });

  test("[L3-real] もう気にしない (棚卸し) も既定の一覧から外れる", async ({ page }) => {
    await gotoHome(page);
    await openProblemList(page);
    await openProblem(page, TOPIC.title);

    await page.getByRole("button", { name: "もう気にしない" }).click();
    await expect(page.getByText("棚卸し", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "一覧へ" }).click();
    await expect(page.getByText(TOPIC.title, { exact: true })).toHaveCount(0);
  });

  test("[L3-real] 棚卸し済みの困りごとをまた話したら再燃し、追跡に戻る (UC-03 事後条件)", async ({
    page,
  }) => {
    // 無いと: 抽出結果レビューが「再燃」と表示するのに、一覧の既定 (追跡中のみ) には
    //         現れず棚卸しのまま埋もれる。「また話している」ことに気づかせられない。
    await gotoHome(page);
    await startConsultationAndSay(page, TOPIC.again);
    await extractProblems(page);

    await expect(page.getByText("既存に追加", { exact: true })).toBeVisible();
    await expect(page.getByText("再燃", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "困りごと一覧へ" }).click();
    await expect(page).toHaveURL(/\/problems$/);
    // 「棚卸し済みも表示」を入れなくても出てくる = 追跡に戻っている
    await expect(page.getByText(TOPIC.title, { exact: true })).toBeVisible();

    await openProblem(page, TOPIC.title);
    await expect(page.getByText("追跡中", { exact: true })).toBeVisible();
  });

  test("[L3-real] タイトルとテーマを直せる (事後トリアージ)", async ({ page }) => {
    // 無いと: AI が付けた見出し・分類を直せず、自分の言葉で持てない一覧になる
    await gotoHome(page);
    await openProblemList(page);
    await openProblem(page, TOPIC.title);

    await page.getByRole("button", { name: "タイトルを編集" }).click();
    const input = page.getByRole("textbox").first();
    await input.fill("家計の見通しが立たない");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("家計の見通しが立たない", { exact: true })).toBeVisible();

    await page.getByRole("combobox", { name: "主テーマ" }).click();
    await page.getByRole("option", { name: "日常・生活", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "主テーマ" })).toHaveText("日常・生活");

    // 一覧にも反映されている (詳細だけ直って一覧が古いまま、を防ぐ)
    await page.getByRole("button", { name: "一覧へ" }).click();
    await expect(page.getByText("家計の見通しが立たない", { exact: true })).toBeVisible();
  });
});
