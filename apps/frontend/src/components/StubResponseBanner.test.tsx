// @vitest-environment jsdom
/**
 * [L1] StubResponseBanner + stubStatus ストア — stub 応答の可視化 (#146)。
 *
 * 無いと何が静かに通るか: BFF が stub 応答 (`stubbed: true`) を返しているのに
 * バナーが出ない退行。stub でも会話 UI は普通に動き続けるため、「frontend は real・
 * BFF は stub」の組み合わせ (#118 の実障害) で偽応答が本物のふりをしても誰も気づかない。
 * 逆に実応答でバナーが出続ける退行 (下ろし忘れ) もここで止める。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { reportStubbedResponse, resetStubbedResponse } from "../api/stubStatus";
import { StubResponseBanner } from "./StubResponseBanner";

afterEach(() => {
  resetStubbedResponse();
  cleanup();
});

describe("[L1] StubResponseBanner", () => {
  it("既定 (実応答のみ) では何も描画しない", () => {
    render(<StubResponseBanner />);
    expect(screen.queryByText(/スタブ応答を表示中/)).toBeNull();
  });

  it("stub 応答を受け取ると警告バナーを表示し、実応答で消える", () => {
    render(<StubResponseBanner />);

    act(() => reportStubbedResponse(true));
    expect(screen.getByText("AI サービス未接続 — スタブ応答を表示中")).toBeTruthy();
    expect(document.querySelector('[data-stub-banner="visible"]')).not.toBeNull();

    // 実応答 (フラグ無し = undefined) で下ろす — BFF 復旧後にバナーが残らない
    act(() => reportStubbedResponse(undefined));
    expect(screen.queryByText(/スタブ応答を表示中/)).toBeNull();
  });
});
