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
 * [L3-real] UC-02 溜まった困りごとを見返す (docs/design/use_cases.md UC-02)。
 *
 * 読み取り専用の UC だが、**一覧の絞り込みと詳細の中身**が UC-03/04/05 の入口になる。
 * 詳細の主役は Mention タイムライン (= 再出現履歴 + 感情の推移 / problem-detail.mdx)。
 *
 * 無いと静かに通るもの: BFF の `problem.list` / `problem.get` が壊れても、mock 版 E2E は
 * mockApi の配列を返し続けるので気づけない。実環境でだけ一覧が空・詳細が開かない。
 */

const TOPIC = {
  utterance: "上司に言いたいことが言えない",
  title: "職場で言いたいことを言えない",
  theme: "人間関係",
  statement: "職場で自分の考えを伝えられず、飲み込んでしまう",
};

/**
 * 事前条件「1 件以上の Problem が蓄積されている」は UC-01 の導線そのもので 1 回だけ作る。
 * 毎テスト作り直すと同じ題材が既存 Problem に寄って言及回数が増え続け、
 * 「言及の履歴（1件）」のような**中身の検証が書けなくなる** (BFF の Problem は
 * プロセス共有の in-memory / #165)。そのため describe.serial + 1 回だけの seed にする。
 */
let seeded = false;

test.describe.serial("UC-02 溜まった困りごとを見返す", () => {
  test.beforeEach(async ({ page }) => {
    await gotoHome(page);

    if (!seeded) {
      await startConsultationAndSay(page, TOPIC.utterance);
      await extractProblems(page);
      seeded = true;
      await gotoHome(page);
    }

    await openProblemList(page);
  });

  test("[L3-real] 一覧に状態・テーマ・最終言及が並び、テーマで絞り込める", async ({ page }) => {
    await expect(page.getByText(TOPIC.title, { exact: true })).toBeVisible();
    await expect(page.getByText(/^最終言及 /).first()).toBeVisible();

    // 1. フィルタ指定 → 2. 絞り込み。別 spec が作った困りごとが混ざっても、
    //    テーマで切れば対象だけになる (切れなければフィルタが効いていない)。
    await page.getByRole("combobox", { name: "テーマ" }).click();
    await page.getByRole("option", { name: TOPIC.theme, exact: true }).click();
    await expect(page.getByText(TOPIC.title, { exact: true })).toBeVisible();
    await expect(page.getByText("お金の先行きが不安", { exact: true })).toHaveCount(0);
  });

  test("[L3-real] 該当ゼロ件なら空状態を案内する (代替フロー 2a)", async ({ page }) => {
    // 無いと: 0 件のときに何も出ず、読み込み中なのか空なのか判断できない
    await page.getByRole("combobox", { name: "テーマ" }).click();
    await page.getByRole("option", { name: "家族・パートナー", exact: true }).click();

    await expect(page.getByText("該当する困りごとはありません。")).toBeVisible();
  });

  test("[L3-real] 詳細に言及の履歴 (Mention タイムライン) と感情が出る", async ({ page }) => {
    // 4〜5. Problem を選ぶ → 詳細 (起源セッション・再出現履歴) を表示する
    await openProblem(page, TOPIC.title);

    await expect(page.getByText(TOPIC.statement).first()).toBeVisible();
    await expect(page.getByText("言及の履歴（1件）")).toBeVisible();
    // 「種」= 最初の Mention。来歴が辿れることが Problem 中心モデルの前提 (ADR 0007)
    await expect(page.getByText("種", { exact: true })).toBeVisible();
    await expect(page.getByText(`「${TOPIC.utterance}」`)).toBeVisible();
    await expect(page.getByText("もどかしさ", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("言及 1 回")).toBeVisible();

    // 戻り道も踏む (戻れないと一覧 → 詳細 → 一覧の回遊が成立しない)
    await page.getByRole("button", { name: "一覧へ" }).click();
    await expect(page).toHaveURL(/\/problems$/);
  });

  test("[L3-real] 棚卸し済みは既定で隠れ、トグルで見返せる", async ({ page }) => {
    // 無いと: 解決した困りごとが一覧に残り続け、「今の困りごと」が見えなくなる
    await openProblem(page, TOPIC.title);
    await page.getByRole("button", { name: "解決した" }).click();
    await expect(page.getByText("解決", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "一覧へ" }).click();
    await expect(page.getByText(TOPIC.title, { exact: true })).toHaveCount(0);

    await showArchivedProblems(page);
    await expect(page.getByText(TOPIC.title, { exact: true })).toBeVisible();
  });
});
