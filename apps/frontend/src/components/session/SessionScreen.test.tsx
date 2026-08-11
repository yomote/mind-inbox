// @vitest-environment jsdom
/**
 * [単体] SessionScreen — 下書き更新の未読通知 (dialogue-session.mdx §5.8 / ADR 0039 D4)。
 *
 * 無いと何が静かに通るか: モバイルで「整理中」タブを一度開いた後、**件数が変わらない
 * 更新** (カードの中身だけが変わる / 1 件が別の困りごとに差し替わる) が通知されない。
 * バッジが出ないだけで描画は壊れないため、テストが無ければ静かに通る (PR #282 P2-b)。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ExtractionResult } from "../../api";
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
  }) as ExtractionResult;

function renderScreen(preview: ExtractionResult | null) {
  const view = render(
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
      onDraftMessageChange={() => {}}
      onSendMessage={() => {}}
      onToggleTtsEnabled={() => {}}
      onStopSpeaking={() => {}}
      onCrisisSupport={() => {}}
      onPause={() => {}}
      onExtract={() => {}}
    />,
  );
  return {
    ...view,
    unseen: () => screen.getByTestId("preview-tab").getAttribute("data-preview-unseen"),
    openPreviewTab: () => fireEvent.click(screen.getByTestId("preview-tab")),
  };
}

describe("[単体] SessionScreen — 下書き更新の未読通知", () => {
  it("件数が同じでも、中身が更新されたら未読になる", () => {
    const { rerender, unseen, openPreviewTab } = renderScreen(draft("最初の下書き"));

    // 一度開いて既読にする。
    openPreviewTab();
    expect(unseen()).toBe("false");

    // 対話タブに戻り、**件数据え置き**で内容だけ変わった更新を受け取る。
    fireEvent.click(screen.getByRole("tab", { name: "対話" }));
    rerender(
      <SessionScreen
        session={session}
        draftMessage=""
        loading={false}
        speaking={false}
        ttsEnabled
        voiceError={null}
        previewEnabled
        preview={draft("更新された下書き")}
        previewStatus="idle"
        onRefreshPreview={() => {}}
        onDraftMessageChange={() => {}}
        onSendMessage={() => {}}
        onToggleTtsEnabled={() => {}}
        onStopSpeaking={() => {}}
        onCrisisSupport={() => {}}
        onPause={() => {}}
        onExtract={() => {}}
      />,
    );

    expect(unseen()).toBe("true");
  });

  it("プレビュータブを開いている間の更新は未読にしない (その場で見えている)", () => {
    const { rerender, unseen, openPreviewTab } = renderScreen(draft("最初の下書き"));
    openPreviewTab();

    rerender(
      <SessionScreen
        session={session}
        draftMessage=""
        loading={false}
        speaking={false}
        ttsEnabled
        voiceError={null}
        previewEnabled
        preview={draft("開いたまま更新")}
        previewStatus="idle"
        onRefreshPreview={() => {}}
        onDraftMessageChange={() => {}}
        onSendMessage={() => {}}
        onToggleTtsEnabled={() => {}}
        onStopSpeaking={() => {}}
        onCrisisSupport={() => {}}
        onPause={() => {}}
        onExtract={() => {}}
      />,
    );

    expect(unseen()).toBe("false");
  });

  it("下書きがまだ無い間は未読にならない", () => {
    const { unseen } = renderScreen(null);
    expect(unseen()).toBe("false");
  });
});
