// @vitest-environment jsdom
/**
 * [単体] 承認カードの表示条件と 2 ボタンの意味 (#82 / G1 / dialogue-session.mdx §5.9)。
 *
 * 無いと何が静かに通るか: (1) 承認要求が無いときにも出る / 有るのに出ない、
 * (2) 「承認して実行」と「却下する」が同じ値を送る (真偽反転)。どちらも画面は
 * 普通に描画され、押せば返事も返るため、目視でも自動テストでも気づけない。
 * これは押した瞬間に副作用ツールが走る操作なので、反転は「却下したのに実行された」
 * になる。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionScreen } from "./SessionScreen";

afterEach(cleanup);

const session = { id: "s1", title: "相談セッション", messages: [] };

const request = {
  id: "appr-1",
  description: "「send_reply」を実行するには承認が必要です。実行してよろしいですか？",
};

function renderScreen(pendingApproval: typeof request | null, onRespond = vi.fn()) {
  render(
    <SessionScreen
      session={session}
      draftMessage=""
      loading={false}
      speaking={false}
      ttsEnabled
      voiceError={null}
      pendingApproval={pendingApproval}
      onRespondToApproval={onRespond}
      onDraftMessageChange={() => {}}
      onSendMessage={() => {}}
      onToggleTtsEnabled={() => {}}
      onStopSpeaking={() => {}}
      onCrisisSupport={() => {}}
      onPause={() => {}}
      onExtract={() => {}}
    />,
  );
  return { onRespond };
}

describe("[単体] SessionScreen — 副作用ツールの承認カード", () => {
  it("承認要求が無い間はカードを出さない", () => {
    renderScreen(null);

    expect(screen.queryByTestId("approval-request")).toBeNull();
  });

  it("承認要求が来たら「何を実行しようとしているか」をカード内に表示する", () => {
    renderScreen(request);

    const card = screen.getByTestId("approval-request");
    // 吹き出しを遡らないと分からない承認カードにしない (§5.9)
    expect(card.textContent).toContain("send_reply");
    expect(card.textContent).toContain("承認が必要です");
    // 注記は MDX §5.9 の文言そのもの (ADR 0005: MDX が真実)。片方だけ書き換えると
    // 「押すまで実行されない」という約束の言い回しが仕様と画面でずれる。
    expect(card.textContent).toContain("承認するまで、この操作は行われません。");
  });

  it("「承認して実行」は true、「却下する」は false を送る", async () => {
    const { onRespond } = renderScreen(request);
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "承認して実行" }));
    expect(onRespond).toHaveBeenLastCalledWith(true);

    await user.click(screen.getByRole("button", { name: "却下する" }));
    expect(onRespond).toHaveBeenLastCalledWith(false);
  });
});
