// @vitest-environment jsdom
/**
 * [単体] SessionScreen — 下書き更新の未読通知 (dialogue-session.mdx §5.8 / ADR 0039 D4) と
 * 承認要求の到着時のタブ復帰 (§5.9)。
 *
 * 無いと何が静かに通るか:
 * - 未読通知: モバイルで「整理」タブを一度開いた後、**件数が変わらない更新**
 *   (カードの中身だけが変わる / 1 件が別の困りごとに差し替わる) が通知されない。
 *   バッジが出ないだけで描画は壊れないため、テストが無ければ静かに通る (PR #282 P2-b)。
 * - タブ復帰: 「整理」タブ表示中に承認要求が届いても対話ペインは display:none のままで、
 *   カードは DOM にあるのに**画面には出ない**。サーバは承認待ちで止まっているのに
 *   ユーザーには何も伝わらない (PR #416 Codex P2)。DOM 上はカードが存在するので、
 *   タブの選択状態を見ないと静かに通る。
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ApprovalRequest, ExtractionResult } from "../../api";
import { SessionScreen } from "./SessionScreen";

afterEach(cleanup);

const session = { id: "s1", title: "相談セッション", messages: [] };

/** 件数は同じで中身だけ違う下書きを作る (件数比較では区別できない更新)。 */
const draft = (statement: string): ExtractionResult =>
  ({
    sessionId: "s1",
    items: [
      {
        mention: {
          id: "m1",
          sessionId: "s1",
          dumpId: "s1",
          createdAt: "2026-01-01T00:00:00.000Z",
          statement,
          excerpt: "そう話しました",
          affect: { label: "不安", valence: "negative", intensity: 0.5 },
          proposedTheme: "仕事・キャリア",
          proposedTags: [],
          problemId: "p1",
          groupingConfidence: null,
        },
        grouping: {
          kind: "new",
          problemId: "p1",
          problemTitle: statement,
          problemTheme: "仕事・キャリア",
          isRecurrence: false,
          mentionCount: 1,
          reignited: false,
          groupingConfidence: null,
        },
      },
    ],
    newProblemCount: 1,
    updatedProblemCount: 0,
    thinkingMap: null,
  }) as ExtractionResult;

type ScreenOverrides = {
  preview?: ExtractionResult | null;
  pendingApproval?: ApprovalRequest | null;
};

const screenEl = ({ preview = null, pendingApproval = null }: ScreenOverrides) => (
  <SessionScreen
    session={session}
    draftMessage=""
    loading={false}
    speaking={false}
    ttsEnabled
    voiceError={null}
    previewEnabled
    preview={preview}
    previewStatus="idle"
    onRefreshPreview={() => {}}
    pendingApproval={pendingApproval}
    onRespondToApproval={() => {}}
    onDraftMessageChange={() => {}}
    onSendMessage={() => {}}
    onToggleTtsEnabled={() => {}}
    onStopSpeaking={() => {}}
    onCrisisSupport={() => {}}
    onPause={() => {}}
    onExtract={() => {}}
  />
);

function renderScreen(overrides: ScreenOverrides) {
  const view = render(screenEl(overrides));
  return {
    ...view,
    rerenderScreen: (next: ScreenOverrides) => view.rerender(screenEl(next)),
    unseen: () => screen.getByTestId("preview-tab").getAttribute("data-preview-unseen"),
    openPreviewTab: () => fireEvent.click(screen.getByTestId("preview-tab")),
    openDialogueTab: () => fireEvent.click(screen.getByRole("tab", { name: "対話" })),
    // タブの選択状態が「どちらのペインが見えているか」の真実 (もう一方は display:none)。
    // 右ペインの中にもタブ (AI の整理 / 下書き / #433) があるので、外側のタブ群に絞る。
    selectedTab: () =>
      within(screen.getByTestId("session-tabs")).getByRole("tab", { selected: true }).textContent,
  };
}

describe("[単体] SessionScreen — 下書き更新の未読通知", () => {
  it("件数が同じでも、中身が更新されたら未読になる", () => {
    const { rerenderScreen, unseen, openPreviewTab, openDialogueTab } = renderScreen({
      preview: draft("最初の下書き"),
    });

    // 一度開いて既読にする。
    openPreviewTab();
    expect(unseen()).toBe("false");

    // 対話タブに戻り、**件数据え置き**で内容だけ変わった更新を受け取る。
    openDialogueTab();
    rerenderScreen({ preview: draft("更新された下書き") });

    expect(unseen()).toBe("true");
  });

  it("プレビュータブを開いている間の更新は未読にしない (その場で見えている)", () => {
    const { rerenderScreen, unseen, openPreviewTab } = renderScreen({
      preview: draft("最初の下書き"),
    });
    openPreviewTab();

    rerenderScreen({ preview: draft("開いたまま更新") });

    expect(unseen()).toBe("false");
  });

  it("下書きがまだ無い間は未読にならない", () => {
    const { unseen } = renderScreen({ preview: null });
    expect(unseen()).toBe("false");
  });
});

// ---- 承認要求の到着とタブ (§5.9 / PR #416 Codex P2) --------------------------

const approval = (id: string): ApprovalRequest => ({
  id,
  description: "山田さんへ返信を送ります。よろしいですか?",
});

describe("[単体] SessionScreen — 承認要求が届いたら対話タブへ戻す", () => {
  it("「整理」タブ表示中に承認要求が届くと対話タブへ戻る", () => {
    // 無いと: カードは DOM に出るが display:none の裏なので画面には現れない。
    // サーバは承認待ちで止まったまま、ユーザーは押すべきボタンの存在を知れない。
    const { rerenderScreen, openPreviewTab, selectedTab } = renderScreen({
      preview: draft("最初の下書き"),
    });

    openPreviewTab();
    expect(selectedTab()).toContain("整理");

    rerenderScreen({ preview: draft("最初の下書き"), pendingApproval: approval("appr-1") });

    expect(selectedTab()).toBe("対話");
    expect(screen.getByTestId("approval-request")).toBeTruthy();
  });

  it("承認待ちのまま「整理」タブへ移ることはできる (pending 中の固定にしない)", () => {
    // 無いと: 「到着時だけ戻す」が「pending の間ずっと対話タブに固定」に化けても
    // 上のテストは通る。承認を保留したまま下書きを見に行けなくなる退行を止める。
    const { openPreviewTab, rerenderScreen, selectedTab } = renderScreen({
      preview: draft("最初の下書き"),
      pendingApproval: approval("appr-1"),
    });

    openPreviewTab();
    expect(selectedTab()).toContain("整理");

    // 同じ承認が出たままの再描画 (下書きの更新など) では引き戻さない。
    rerenderScreen({ preview: draft("更新された下書き"), pendingApproval: approval("appr-1") });

    expect(selectedTab()).toContain("整理");
  });

  it("承認が解決したあとに次の要求が届いたら、また対話タブへ戻る", () => {
    // 無いと: 「初回だけ戻す」実装 (フラグの立てっぱなし) が通ってしまい、
    // 2 回目以降の承認要求で同じ見落としが起きる。
    const { openPreviewTab, rerenderScreen, selectedTab } = renderScreen({
      preview: draft("最初の下書き"),
      pendingApproval: approval("appr-1"),
    });

    // 1 件目を解決 → 整理タブへ移動 → 2 件目が届く。
    rerenderScreen({ preview: draft("最初の下書き"), pendingApproval: null });
    openPreviewTab();
    rerenderScreen({ preview: draft("最初の下書き"), pendingApproval: approval("appr-2") });

    expect(selectedTab()).toBe("対話");
  });
});
