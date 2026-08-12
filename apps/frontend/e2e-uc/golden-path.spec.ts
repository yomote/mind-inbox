import {
  expect,
  extractProblems,
  gotoHome,
  openProblem,
  startConsultationAndSay,
  test,
} from "./fixtures";

/**
 * [L3-real] **ユースケースのゴールデンパスを 1 本で通す** (ADR 0034)。
 *
 * uc01〜uc05 の spec は UC ごとに深く見るが、**「1 人のユーザーが端から端まで
 * 歩けるか」は誰も見ていなかった**。UC 単位で緑でも、UC と UC の継ぎ目
 * (抽出結果 → 一覧 → 詳細 → 次の一歩 → 棚卸し → 再訪) が切れていれば
 * プロダクトとしては成立しない。ここがその継ぎ目を固定する。
 *
 * この道は `docs/design/requirements.md` の FR-1〜FR-9 と 1:1 で対応する:
 *
 *   吐き出す(FR-1) → 抽出+グルーピング(FR-2/3) → 気づき(FR-7)
 *     → 一覧(FR-5) → 詳細 → 次の一歩(FR-9) → 棚卸し(FR-6)
 *     → 後日また話す → 再出現(FR-7)
 *
 * **ここに入らないもの**は UI にも無いのが正 — 整理結果 / 行動プラン(整理結果起点) /
 * 履歴・振り返り は UC にも FR にも対応が無く、ADR 0034 で撤去した。
 * (永続化 FR-4 だけはこの層では見られない。BFF が in-memory なので L4 の担当)
 */

const TOPIC = {
  first: "資格の勉強を始めたのに続かない",
  again: "また学び直しが身につかないままで焦る",
  title: "学び直しが続かない",
  theme: "自己理解・生き方",
};

test.describe.serial("ユースケースのゴールデンパス (UC-01 → 02 → 05 → 04 → 03)", () => {
  test("[L3-real] 吐き出しから再出現の気づきまで、1 人分の道が端から端まで通る", async ({
    page,
  }) => {
    await gotoHome(page);

    // ── UC-01 吐き出す → 困りごとになる ─────────────────────────────
    await startConsultationAndSay(page, TOPIC.first);
    await extractProblems(page);
    await expect(page.getByText("今回の話から 1 件の困りごとを見つけました")).toBeVisible();
    await expect(page.getByText(TOPIC.title, { exact: true })).toBeVisible();

    // ── UC-02 一覧 → 詳細 ───────────────────────────────────────────
    await page.getByRole("button", { name: "困りごと一覧へ" }).click();
    await expect(page).toHaveURL(/\/problems$/);
    await expect(page.getByText(TOPIC.theme, { exact: true }).first()).toBeVisible();
    await openProblem(page, TOPIC.title);
    await expect(page.getByText("言及の履歴（1件）")).toBeVisible();

    // ── UC-05 次の一歩 (状態は変えない = 派生物) ────────────────────
    await page.getByRole("button", { name: "次の一歩を作る" }).click();
    await expect(page.getByText("次の一歩", { exact: true })).toBeVisible();
    await expect(page.getByText("追跡中", { exact: true })).toBeVisible();

    // ── UC-04 棚卸し → 既定の一覧から外れる ─────────────────────────
    await page.getByRole("button", { name: "解決した" }).click();
    await expect(page.getByText("解決", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "一覧へ" }).click();
    await expect(page.getByText(TOPIC.title, { exact: true })).toHaveCount(0);

    // ── UC-03 後日また話す → 再出現に気づき、追跡に戻る ─────────────
    await gotoHome(page);
    await startConsultationAndSay(page, TOPIC.again);
    await extractProblems(page);
    await expect(page.getByText("既存に追加", { exact: true })).toBeVisible();
    // 回数バッジは MUI アイコン (Repeat) + 「N回目」— 絵文字ではない (#237 / ui_specs 共通規約)
    const recurrenceChip = page.locator(".MuiChip-root", { hasText: "2回目" });
    await expect(recurrenceChip).toBeVisible();
    await expect(recurrenceChip.locator('svg[data-testid="RepeatIcon"]')).toBeVisible();
    await expect(page.getByText("再燃", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "困りごと一覧へ" }).click();
    await expect(page.getByText(TOPIC.title, { exact: true })).toBeVisible();
    await openProblem(page, TOPIC.title);
    await expect(page.getByText("追跡中", { exact: true })).toBeVisible();
    await expect(page.getByText("言及の履歴（2件）")).toBeVisible();
    // 次の一歩は棚卸し・再燃をまたいでも残る (Problem に紐づく派生物であって、
    // セッションの持ち物ではない — 旧導線ではここが履歴側に居て切れていた)
    await expect(page.getByText("次の一歩", { exact: true })).toBeVisible();
  });

  test("[L3-real] UC に無い旧導線が UI から消えている (ADR 0034)", async ({ page }) => {
    // 無いと: 撤去した「整理結果へ」「履歴・振り返り」が復活しても誰も気づかない。
    // 「整理結果へ」は下流 (/organize) が会話をプロセスメモリから引くため 404 を返す
    // 壊れた導線だった (2026-08-10 に PO が実環境で踏んだ)。
    await gotoHome(page);
    await expect(page.getByRole("button", { name: "履歴・振り返り" })).toHaveCount(0);

    await page.getByRole("button", { name: "新しい相談を始める" }).click();
    await expect(page).toHaveURL(/\/consultations\/current$/);
    await expect(page.getByRole("button", { name: "整理結果へ" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "困りごとを抽出" })).toBeVisible();

    // 直接 URL を叩いても復活しない (ルート自体が無い)
    await page.goto("/history");
    await expect(page).toHaveURL(/\/home$/);
    await page.goto("/consultations/current/result");
    await expect(page).toHaveURL(/\/home$/);
  });
});
