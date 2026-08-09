import {
  dropAiAgentSessions,
  expect,
  extractProblems,
  gotoHome,
  startConsultationAndSay,
  test,
} from "./fixtures";

/**
 * [L3-real] UC-01 モヤモヤを吐き出して困りごとに整理する
 * (docs/design/use_cases.md UC-01)。
 *
 * 事後条件: **Mention が保存され、自動グルーピングで Problem が open 生成される**。
 * ここが通らないと、以降の UC (見返す / 気づく / 棚卸し / 次の一歩) は全部前提を失う。
 *
 * 無いと静かに通るもの: mock 版 E2E は mockApi の配列を見ているだけなので、
 * 「フロント → tRPC → BFF の extract → materializeExtraction → Problem リポジトリ」
 * のどこが切れても緑のまま。実環境でだけ抽出が空振りする。
 */

// この spec が占有する題材 (spec 間で Problem が混ざらないように分けている)
const TOPIC = {
  utterance: "家事が全然回らなくて部屋が荒れてる",
  problemTitle: "家事が回らず部屋が荒れる",
  theme: "日常・生活",
};

test.describe("UC-01 モヤモヤを吐き出して困りごとに整理する", () => {
  test("[L3-real] 吐き出し → Mention 抽出 → Problem が open で生成され、一覧に載る", async ({
    page,
  }) => {
    await gotoHome(page);

    // 1〜2. 吐き出す → System が Mention を抽出する
    await startConsultationAndSay(page, TOPIC.utterance);
    await extractProblems(page);

    // 4. 結果の提示 — 何件見つけたか / 新規か既存更新か
    await expect(page.getByText("今回の話から 1 件の困りごとを見つけました")).toBeVisible();
    await expect(page.getByText("新規 1 件 / 既存に追加 0 件")).toBeVisible();
    await expect(page.getByText(TOPIC.problemTitle, { exact: true })).toBeVisible();
    // 来歴 (どの発話から出たか) が見えること — 「AI が勝手に決めた」に見せない
    await expect(page.getByText(`「${TOPIC.utterance}」`)).toBeVisible();

    // 事後条件: Problem が open で一覧に載っている (既定の一覧は open のみを出す)
    await page.getByRole("button", { name: "困りごと一覧へ" }).click();
    await expect(page).toHaveURL(/\/problems$/);
    await expect(page.getByText(TOPIC.problemTitle, { exact: true })).toBeVisible();
    await expect(page.getByText(TOPIC.theme, { exact: true }).first()).toBeVisible();
  });

  test("[L3-real] ai-agent が会話を忘れていても抽出できる (レプリカが落ちた状況)", async ({
    page,
    request,
  }) => {
    // 無いと: 「対話はできたのに抽出だけ失敗する」(#183 の実症状) を**この層では踏めない**。
    //         fake は実サービスと同じくセッション履歴をプロセス内に持つため、履歴が生きて
    //         いる前提でしか成功パスを通しておらず、scale-to-zero でレプリカが落ちる本番
    //         だけで壊れる。会話をリクエストに載せる実装 (#183) が剥がれても緑のまま通る。
    // 題材は他 spec と重ねない (BFF の Problem リポジトリは全 spec で共有の in-memory)。
    // 重ねると別 spec の件数・並び・回数バッジを動かして、そちらを落とす。
    const utterance = "運動する時間が全然取れていない";
    const problemTitle = "運動する時間が取れない";

    await gotoHome(page);
    await startConsultationAndSay(page, utterance);

    // ここでレプリカが落ちた / 別レプリカに当たった状況を作る
    await dropAiAgentSessions(request);

    await extractProblems(page);

    // 会話はブラウザから同送されているので、履歴が無くても抽出は成立する
    await expect(page.getByText("今回の話から 1 件の困りごとを見つけました")).toBeVisible();
    await expect(page.getByText(problemTitle, { exact: true })).toBeVisible();

    // 後片付け: Problem リポジトリは全 spec で共有の in-memory なので、
    // 作ったものを残すと後続 spec の件数・並び・回数バッジを動かす。
    await page.getByRole("button", { name: "これは違う（却下）" }).click();
    await expect(page.getByText(problemTitle, { exact: true })).toHaveCount(0);
  });

  test("[L3-real] 抽出ゼロ件なら Problem を作らず受け止めだけ返す (代替フロー 2a)", async ({
    page,
  }) => {
    // 無いと: 雑談まで Problem 化して一覧がノイズで埋まる (concept_deck §3「まず受け止める」に反する)
    await gotoHome(page);

    await startConsultationAndSay(page, "今日は天気が良くて気分がいいです");
    await extractProblems(page);

    await expect(
      page.getByText("今回の話からは、追跡する困りごとは見つかりませんでした。受け止めました。"),
    ).toBeVisible();
    await expect(page.getByText(/件の困りごとを見つけました/)).toHaveCount(0);
  });

  test("[L3-real] 誤抽出はその場で却下でき、一覧に残らない (基本フロー 5 / 事後トリアージ)", async ({
    page,
  }) => {
    // 無いと: 「これは違う」を押しても消えず、ユーザーが直せない一覧になる
    // (却下は BFF の problem.triage:dismiss を通る = mock 版では一度も踏まない経路)
    const dismissed = { utterance: "満員電車がきつい", title: "通勤がしんどい" };
    await gotoHome(page);

    await startConsultationAndSay(page, dismissed.utterance);
    await extractProblems(page);
    await expect(page.getByText(dismissed.title, { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "これは違う（却下）" }).click();
    await expect(page.getByText(dismissed.title, { exact: true })).toHaveCount(0);

    await page.getByRole("button", { name: "困りごと一覧へ" }).click();
    await expect(page).toHaveURL(/\/problems$/);
    await expect(page.getByText(dismissed.title, { exact: true })).toHaveCount(0);
  });
});
