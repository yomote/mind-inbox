// @vitest-environment jsdom
/**
 * [L1] SessionMessages — マスコット + 吹き出しの配置規則 (dialogue-session.mdx §5.6 / #229)。
 *
 * 無いと何が静かに通るか: (1) マスコットがユーザー発話にも付く / AI 発話から消える、
 * (2) 状態がアクティブでない過去の吹き出しに反映される (全員が口パクする)、
 * (3) 生成待ちのプレースホルダが消えて「送信したのに無反応の空白」が復活する、
 * (4) 開始直後 (#241 で挨拶を撤去した後) の待機マスコットが消えて無言の白画面に戻る —
 * いずれも描画は壊れないため、テストが無ければ静かに通る。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ChatMessage } from "../../api";
import { beginStreamingReply, clearStreamingReply } from "../../api/streamingReply";
import { SessionMessages } from "./SessionMessages";

afterEach(() => {
  clearStreamingReply();
  cleanup();
});

const msg = (id: string, role: "user" | "assistant", text: string): ChatMessage => ({
  id,
  role,
  text,
  createdAt: "2026-01-01T00:00:00.000Z",
});

const conversation = [
  msg("u-1", "user", "眠れない日が続いている"),
  msg("a-1", "assistant", "どうしましたか"),
  msg("u-2", "user", "仕事のことが頭から離れない"),
  msg("a-2", "assistant", "詳しく聞かせてください"),
];

function mascotStates(container: HTMLElement): string[] {
  return [...container.querySelectorAll("[data-mascot-state]")].map(
    (el) => el.getAttribute("data-mascot-state") ?? "",
  );
}

describe("[L1] SessionMessages マスコット配置", () => {
  it("AI メッセージにだけマスコットが付き、状態は最新の AI 吹き出しにだけ反映される", () => {
    const { container } = render(
      <SessionMessages messages={conversation} mascotState="speaking" />,
    );

    // AI 2 件ぶんのマスコット (ユーザー発話には付かない)
    expect(mascotStates(container)).toEqual(["idle", "speaking"]);
    // 本文と話者ラベルは従来どおり
    expect(screen.getByText("どうしましたか")).toBeTruthy();
    expect(screen.getAllByText("ガイド")).toHaveLength(2);
    expect(screen.getAllByText("あなた")).toHaveLength(2);
  });

  it("生成待ち (最後がユーザー発話 + preparing) はプレースホルダ吹き出しを出す", () => {
    const waiting = conversation.slice(0, 3); // 最後が user
    const { container } = render(<SessionMessages messages={waiting} mascotState="preparing" />);

    expect(screen.getByTestId("mascot-thinking")).toBeTruthy();
    // プレースホルダのマスコットが preparing、既存 AI 吹き出しは idle
    expect(mascotStates(container)).toEqual(["idle", "preparing"]);
  });

  it("読み上げ中 (最後が AI 発話) はプレースホルダを出さず、最新 AI 吹き出しに反映する", () => {
    render(<SessionMessages messages={conversation} mascotState="speaking" />);
    expect(screen.queryByTestId("mascot-thinking")).toBeNull();
  });

  it("開始直後 (メッセージ 0 件 / #241) は待機マスコットだけを出す — 挨拶は出さない", () => {
    const { container } = render(<SessionMessages messages={[]} mascotState="idle" />);
    expect(screen.getByTestId("mascot-empty-state")).toBeTruthy();
    expect(mascotStates(container)).toEqual(["idle"]);
    expect(screen.queryByText("ガイド")).toBeNull();
  });

  it("ストリーミング中の吹き出しがマスコットを持ち、既存の AI 吹き出しは idle に落ちる", () => {
    beginStreamingReply("a-3");
    const { container } = render(
      <SessionMessages messages={conversation} mascotState="preparing" />,
    );

    // 既存 AI 2 件 (idle) + ストリーミングバブル (preparing)
    expect(mascotStates(container)).toEqual(["idle", "idle", "preparing"]);
    // ストリーミングキャレットは従来どおり
    expect(screen.getByText("▍")).toBeTruthy();
  });
});
