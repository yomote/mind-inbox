// @vitest-environment jsdom
/**
 * [単体] 選択肢の表示条件と、承認カードとの独立性 (#432-b / dialogue-session.mdx §5.10)。
 *
 * 無いと何が静かに通るか:
 * - 選択肢が 0 件の応答でも空のチップ帯が全ターンに出る (画面は普通に描画される)
 * - 選択肢が出ている間に入力欄が塞がれ、「選ばないと進めない」画面になる
 *   (承認カードの規律をそのままコピーすると起きる。混合が裁定 / PO 2026-08-15)
 * - タップした文言と送られる文言がずれる (押した選択肢と違う発話が会話に残る)
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionScreen } from "./SessionScreen";

afterEach(cleanup);

const session = { id: "s1", title: "相談セッション", messages: [] };

const CHOICES = ["仕事のこと", "家族のこと"];

function renderScreen(
  props: {
    offeredChoices?: string[];
    pendingApproval?: { id: string; description: string } | null;
    loading?: boolean;
  } = {},
) {
  const onSelectChoice = vi.fn();
  render(
    <SessionScreen
      session={session}
      draftMessage=""
      loading={props.loading ?? false}
      speaking={false}
      ttsEnabled
      voiceError={null}
      pendingApproval={props.pendingApproval ?? null}
      onRespondToApproval={() => {}}
      offeredChoices={props.offeredChoices ?? []}
      onSelectChoice={onSelectChoice}
      onDraftMessageChange={() => {}}
      onSendMessage={() => {}}
      onToggleTtsEnabled={() => {}}
      onStopSpeaking={() => {}}
      onCrisisSupport={() => {}}
      onPause={() => {}}
      onExtract={() => {}}
    />,
  );
  return { onSelectChoice };
}

describe("[単体] SessionScreen — AI が提示した選択肢", () => {
  it("選択肢が 0 件の応答では何も出さない", () => {
    renderScreen({ offeredChoices: [] });

    expect(screen.queryByTestId("choice-options")).toBeNull();
  });

  it("選択肢が届いたらそのままボタンとして並べる", () => {
    renderScreen({ offeredChoices: CHOICES });

    expect(screen.getByTestId("choice-options")).toBeTruthy();
    for (const choice of CHOICES) {
      expect(screen.getByRole("button", { name: choice })).toBeTruthy();
    }
  });

  it("タップした文言をそのまま渡す (押した選択肢と送る発話をずらさない)", async () => {
    const { onSelectChoice } = renderScreen({ offeredChoices: CHOICES });

    await userEvent.click(screen.getByRole("button", { name: "家族のこと" }));

    expect(onSelectChoice).toHaveBeenCalledTimes(1);
    expect(onSelectChoice).toHaveBeenCalledWith("家族のこと");
  });

  it("選択肢が出ていても入力欄は塞がない (選ばずに書けるのが仕様)", () => {
    renderScreen({ offeredChoices: CHOICES });

    // 承認カード (§5.9) と対称にしない — こちらはサーバが待っていないので、
    // 押さずに自由記述で答えるのが正常な操作 (混合が裁定)
    const input = screen.getByRole("textbox");
    expect((input as HTMLTextAreaElement).disabled).toBe(false);
  });

  it("承認カードとは別物として出る (承認要求のときに選択肢の帯を出さない)", () => {
    renderScreen({
      offeredChoices: [],
      pendingApproval: { id: "appr-1", description: "「send_reply」を実行しますか?" },
    });

    expect(screen.getByTestId("approval-request")).toBeTruthy();
    expect(screen.queryByTestId("choice-options")).toBeNull();
  });

  it("送信中は押せない (連打で同じ発話が 2 通送られない)", () => {
    renderScreen({ offeredChoices: CHOICES, loading: true });

    const button = screen.getByRole("button", { name: "仕事のこと" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});
