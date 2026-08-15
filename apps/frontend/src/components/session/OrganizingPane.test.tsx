// @vitest-environment jsdom
/**
 * [単体] 右ペインのタブ 2 枚 (#433 段階 1 / dialogue-session.mdx §5.8)。
 *
 * 無いと何が静かに通るか:
 * - **既定タブが「AI の整理」であること**: 既定が下書きに戻っても画面は普通に動くので、
 *   PO 裁定 1 (思考の整理過程を既定の視界に置く) が黙って無効化される。
 * - **下書きタブが生きていること**: マップで置き換えてしまうと「この内容で確定」の
 *   「この内容」が画面から消え、確定が再抽出に戻る (ADR 0039 D3 が避けた事故)。
 * - **更新失敗が右ペイン内に出ること**: 会話を止めず、無音にもしない (ADR 0018)。
 *   タブを分けた拍子に警告が片方のタブでしか出なくなっても描画は壊れない。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExtractionResult } from "../../api";
import { OrganizingPane } from "./OrganizingPane";

afterEach(cleanup);

const preview = (): ExtractionResult =>
  ({
    sessionId: "s1",
    items: [
      {
        mention: {
          id: "m1",
          sessionId: "s1",
          dumpId: "s1",
          createdAt: "2026-01-01T00:00:00.000Z",
          statement: "転職のことが頭から離れない",
          excerpt: "転職しようか迷ってて",
          affect: { label: "迷い", valence: "negative", intensity: 0.5 },
          proposedTheme: "仕事・キャリア",
          proposedTags: [],
          problemId: "p1",
          groupingConfidence: null,
        },
        grouping: {
          kind: "new",
          problemId: "p1",
          problemTitle: "転職の迷い",
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
    thinkingMap: {
      nodes: [
        {
          id: "n1",
          kind: "topic",
          label: "転職するかどうか",
          status: "confirmed",
          parentId: null,
          problemId: null,
        },
      ],
    },
  }) as ExtractionResult;

describe("[単体] OrganizingPane — タブ 2 枚の併存", () => {
  it("既定タブは「AI の整理」(下書きカードは出さない)", () => {
    render(<OrganizingPane preview={preview()} status="idle" onRefresh={() => {}} />);

    expect(screen.getByTestId("thinking-map-pane")).toBeTruthy();
    expect(screen.queryByTestId("live-preview-pane")).toBeNull();
  });

  it("「下書き」タブに切り替えると下書きカードが出る (置き換えではなく併存)", () => {
    render(<OrganizingPane preview={preview()} status="idle" onRefresh={() => {}} />);

    fireEvent.click(screen.getByTestId("draft-tab"));

    expect(screen.getByTestId("preview-card")).toBeTruthy();
    expect(screen.queryByTestId("thinking-map-pane")).toBeNull();
  });

  it("「今すぐ整理」はどちらのタブでも 1 つで、同じ更新を呼ぶ", () => {
    // 無いと: タブごとに更新ボタンが増え、「どちらを押したか」で結果が違うように見える
    // (実体は同じ 1 回の preview — 追加 LLM 呼び出し 0 / 裁定 2)。
    const onRefresh = vi.fn();
    render(<OrganizingPane preview={preview()} status="idle" onRefresh={onRefresh} />);

    fireEvent.click(screen.getByRole("button", { name: "今すぐ整理" }));
    fireEvent.click(screen.getByTestId("draft-tab"));
    fireEvent.click(screen.getByRole("button", { name: "今すぐ整理" }));

    expect(onRefresh).toHaveBeenCalledTimes(2);
  });

  it("更新失敗の警告はタブに関係なく右ペイン内に出る", () => {
    const { rerender } = render(
      <OrganizingPane preview={preview()} status="error" onRefresh={() => {}} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("会話はそのまま続けられます");

    fireEvent.click(screen.getByTestId("draft-tab"));
    rerender(<OrganizingPane preview={preview()} status="error" onRefresh={() => {}} />);

    expect(screen.getByRole("alert").textContent).toContain("会話はそのまま続けられます");
  });
});
