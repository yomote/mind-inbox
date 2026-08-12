// @vitest-environment jsdom
/**
 * [L1] MascotAvatar / deriveMascotState — キャラ状態の観測点と状態写像 (#229 / ADR 0039 D5)。
 *
 * 無いと何が静かに通るか: `data-mascot-state` (E2E と ADR 0039 の動作検証が読む機械観測点)
 * が消えたり、TTS の synthesizing/playing がマスコットの preparing/speaking に写らなくなっても、
 * マスコットの絵自体は描画され続けるため誰も気づかない — 「発話待ちが見える」という
 * #229 の目的だけが静かに死ぬ。
 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MascotAvatar } from "./MascotAvatar";
import type { MascotState } from "./MascotAvatar";
import { deriveMascotState } from "./mascotState";

afterEach(cleanup);

describe("[L1] MascotAvatar", () => {
  it.each<MascotState>(["idle", "preparing", "speaking"])(
    "state=%s を data-mascot-state として公開する",
    (state) => {
      const { container } = render(<MascotAvatar state={state} />);
      expect(container.querySelector(`[data-mascot-state="${state}"]`)).not.toBeNull();
    },
  );

  it("状態はアニメーション無しでも区別できる (口の形が状態ごとに変わる)", () => {
    // prefers-reduced-motion でアニメーションを止めても状態が伝わることの静的な根拠。
    const speaking = render(<MascotAvatar state="speaking" />);
    expect(speaking.container.querySelector(".mi-mascot-mouth-open")).not.toBeNull();
    cleanup();
    const idle = render(<MascotAvatar state="idle" />);
    expect(idle.container.querySelector(".mi-mascot-mouth-open")).toBeNull();
  });
});

describe("[L1] deriveMascotState", () => {
  it.each([
    // 読み上げ再生中 (応答待ちでない) は speaking
    { loading: false, ttsStatus: "playing", expected: "speaking" },
    // **前の応答の読み上げ中に次を送った場合は preparing が勝つ** (Codex P2)。
    // 無いと: speaking が返って SessionMessages の「考え中」プレースホルダ
    // (preparing のときだけ出す) が現れず、読み上げ中の送信が無反応に見える退行が静かに通る
    { loading: true, ttsStatus: "playing", expected: "preparing" },
    // AI 生成中 / 音声合成中は preparing (「ずんだもんが準備中…」の区間)
    { loading: true, ttsStatus: "idle", expected: "preparing" },
    { loading: true, ttsStatus: undefined, expected: "preparing" },
    { loading: false, ttsStatus: "synthesizing", expected: "preparing" },
    // どちらでもなければ待機
    { loading: false, ttsStatus: "idle", expected: "idle" },
    { loading: false, ttsStatus: undefined, expected: "idle" },
  ] as const)(
    "loading=$loading ttsStatus=$ttsStatus → $expected",
    ({ loading, ttsStatus, expected }) => {
      expect(deriveMascotState(loading, ttsStatus)).toBe(expected);
    },
  );
});
