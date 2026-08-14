// @vitest-environment jsdom
/**
 * [L2] Layout の「配線」テスト。
 *
 * 無いと何が静かに通るか: hook (voice / consultation) を切り出した後、**呼び出し側の
 * 配線だけが失われても** hook 単体テストは緑のまま通る。実際 PR #152 のレビューで、
 * iOS 音声解錠 `unlock()` が分離の過程で呼び出し元を失いデッドコード化していたのを
 * 指摘された (hook のテスト 8 本はすべて緑だった)。
 * ここでは「ユーザー操作 → hook のどの入口が叩かれるか」だけを固定する。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 認証まわりはテストごとに切り替える (既定は「認証無効ビルド」= 門を通す)。
 * サインアウトの配線だけは**認証有効・非 standalone** でしか通らないので、
 * その 1 本だけ enabled / account を立てる。
 */
const authState = vi.hoisted(() => ({ enabled: false, account: null as { name: string } | null }));

vi.mock("./auth/msal", () => ({
  get authEnabled() {
    return authState.enabled;
  },
  getAccount: () => authState.account,
  initAuth: vi.fn(async () => undefined),
  login: vi.fn(),
  logout: vi.fn(),
  disableInteractiveRecovery: vi.fn(),
}));

vi.mock("./api", () => ({
  startNewConsultation: vi.fn(async () => ({
    id: "s1",
    title: "相談セッション",
    messages: [{ id: "a-1", role: "assistant", text: "どうしましたか", createdAt: "2026-01-01" }],
  })),
  sendMessage: vi.fn(),
  respondToApproval: vi.fn(),
  organizeResult: vi.fn(),
  createActionPlan: vi.fn(),
  extractMentions: vi.fn(),
  loadHistories: vi.fn(async () => []),
  saveHistory: vi.fn(),
  loadProblems: vi.fn(async () => []),
  loadProblem: vi.fn(),
  triageProblem: vi.fn(),
  createProblemPlan: vi.fn(),
  previewSupported: false,
  previewExtraction: vi.fn(),
}));

import { Layout } from "./Layout";
import { respondToApproval, sendMessage, startNewConsultation } from "./api";
import { disableInteractiveRecovery, logout } from "./auth/msal";

/**
 * iOS の解錠は「音量 0 の空発話を speechSynthesis に流す」で表現されるので、
 * speak に渡された utterance の volume で判別する。
 *
 * `autoEnd: false` を渡すと onend を呼ばない = 発話が「再生中のまま」になり、
 * 再生中の画面遷移・停止の配線を検証できる。
 * `events` は speak / cancel の時系列 — cancel が解錠発話を打ち消していないかを見る用。
 */
function speechSynthesisSpy({ autoEnd = true }: { autoEnd?: boolean } = {}) {
  const spoken: { text: string; volume: number }[] = [];
  const events: Array<{ kind: "speak"; volume: number } | { kind: "cancel" }> = [];
  vi.stubGlobal("speechSynthesis", {
    speak: (u: { text: string; volume: number; onend?: (() => void) | null }) => {
      spoken.push({ text: u.text, volume: u.volume });
      events.push({ kind: "speak", volume: u.volume });
      if (autoEnd) u.onend?.();
    },
    cancel: () => {
      events.push({ kind: "cancel" });
    },
    getVoices: () => [],
  });
  vi.stubGlobal(
    "SpeechSynthesisUtterance",
    class {
      text: string;
      lang = "";
      rate = 1;
      volume = 1;
      voice: unknown = null;
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(text: string) {
        this.text = text;
      }
    },
  );
  return { spoken, events };
}

const renderLayout = () =>
  render(
    <MemoryRouter initialEntries={["/home"]}>
      <Layout themeMode="light" onToggleTheme={() => {}} />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  // stubGlobal はファイル内で持ち越される。speechSynthesis の有無をテストごとに
  // 制御したい (「無い環境」で読み上げ失敗を再現するテストがある) ので毎回戻す。
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  authState.enabled = false;
  authState.account = null;
});

// vitest は isolate:false で走るので、DOM は test 間で共有される。
// 片付けないと前のテストのツリー (別画面のヘッダー等) が次のクエリに引っかかる。
afterEach(cleanup);

describe("[L2] Layout — 音声解錠 (iOS) の配線", () => {
  it("相談開始のタップで音声を解錠する", async () => {
    // 無いと: iOS Safari で以降の自動読み上げが無音になる。ブラウザ (headless) では
    //         再生されないので E2E でも気づけない — 配線の有無をここで固定する。
    //         解錠は冪等 (最初のジェスチャ 1 回) なので、3 つの呼び出し口
    //         (相談開始 / 送信 / 読み上げトグル) は同じガードを共有する
    const { spoken } = speechSynthesisSpy();
    renderLayout();

    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));

    await waitFor(() => expect(spoken.some((u) => u.volume === 0)).toBe(true));
  });

  it("解錠は 1 度きり (2 回目以降のタップで無駄な発話をしない)", async () => {
    // 無いと: タップのたびに空の utterance が積まれ、読み上げの頭が欠ける
    const { spoken } = speechSynthesisSpy();
    renderLayout();

    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));
    await waitFor(() => expect(spoken.some((u) => u.volume === 0)).toBe(true));

    spoken.length = 0;
    await userEvent.click(await screen.findByRole("button", { name: /読み上げ(ON|OFF)$/ }));

    expect(spoken.filter((u) => u.volume === 0)).toHaveLength(0);
  });
});

describe("[L2] Layout — 新しい相談の開始で読み上げを止める (#146 レビュー)", () => {
  it("再生中に新しい相談を始めると読み上げが止まる (旧セッションの音声と speaking を持ち込まない)", async () => {
    // 無いと: 前の応答の再生中にホームへ戻って新しい相談を始めると、旧セッションの音声が
    // 鳴り続け、メッセージ 0 件の新セッションのマスコットが speaking のまま表示される退行が
    // 静かに通る (tts と consultation は互いを知らないので、止める配線は Layout にしか無い)。
    speechSynthesisSpy({ autoEnd: false }); // onend が来ない = 再生しっぱなし
    renderLayout();

    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));
    // AI の返事が自動読み上げされ、再生中の表示になる (standalone = ブラウザ読み上げ)
    const chip = await screen.findByTestId("tts-status");
    expect(chip.textContent).toContain("読み上げ中");

    // ホームへ戻り、再生が続いたまま新しい相談を開始する
    await userEvent.click(screen.getByRole("button", { name: "Mind Inbox" }));
    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));

    // 開始タップで読み上げが止まり、「読み上げ中」表示 (= ttsStatus playing) が消える
    await waitFor(() => expect(screen.queryByTestId("tts-status")).toBeNull());
  });

  it("開始時の stop が解錠の無音発話を打ち消さない (stop → unlock の順)", async () => {
    // 無いと: unlock() が無音発話を enqueue して解錠済みマーク (ref) を立てた直後に、
    // stop() の speechSynthesis.cancel() がその発話を取り消す。マークにより以降の
    // unlock() は no-op なので「解錠済みのはずが実際は解錠されていない」状態に固定され、
    // iOS / ブラウザフォールバック環境で最初の読み上げが無音になる退行が静かに通る。
    const { events } = speechSynthesisSpy();
    renderLayout();

    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));

    // 解錠の無音発話 (volume 0) は開始タップの同期処理で enqueue される
    const unlockIndex = events.findIndex((e) => e.kind === "speak" && e.volume === 0);
    expect(unlockIndex).toBeGreaterThanOrEqual(0);
    // 停止 (cancel) は解錠発話より**前** — 後に来ると解錠発話がキューから消される。
    // (後続の自動読み上げ (speakWithBrowser) 内の cancel は実発話が直後に続くので対象外 —
    //  ここで見るのは開始タップの同期区間 = 解錠発話までの並びだけ)
    const cancelsBeforeUnlockOrNever = events
      .slice(0, unlockIndex)
      .filter((e) => e.kind === "cancel").length;
    const cancelsTotalInGesture = events
      .slice(0, unlockIndex + 1)
      .filter((e) => e.kind === "cancel").length;
    expect(cancelsTotalInGesture).toBe(cancelsBeforeUnlockOrNever); // 同期区間の cancel は全て解錠前
    expect(cancelsBeforeUnlockOrNever).toBeGreaterThanOrEqual(1); // stop は呼ばれている (P2-4 の配線)
  });

  it("新しい相談の開始で前セッションの読み上げ警告 (tts.error) を持ち込まない", async () => {
    // 無いと: 前の応答の読み上げが劣化/失敗した警告は stop() では消えず (stop は再生
    // status しか戻さない)、挨拶を撤去した (#241) 新セッションには error を消す契機の
    // speak() も無いため、応答が 1 つも無い新セッションに前セッションの警告が次の返事まで
    // 出続ける退行が静かに通る。speechSynthesis の無い環境 (jsdom 素) が読み上げ失敗の
    // 最短再現 — 警告文言が voiceError として SessionComposer に表示される。
    vi.mocked(startNewConsultation)
      .mockResolvedValueOnce({
        id: "tts-err-1",
        title: "相談セッション",
        messages: [
          { id: "a-err-1", role: "assistant", text: "どうしましたか", createdAt: "2026-01-01" },
        ],
      } as never)
      // 2 セッション目は空 (挨拶なし) = speak の契機が無いことまで含めた再現
      .mockResolvedValueOnce({ id: "tts-err-2", title: "相談セッション", messages: [] } as never);

    renderLayout();
    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));
    // 前セッション: 自動読み上げが失敗し劣化警告が出る
    await screen.findByText("このブラウザは音声読み上げに対応していません。");

    // ホームへ戻り、新しい相談 (空セッション) を開始すると警告は消える
    await userEvent.click(screen.getByRole("button", { name: "Mind Inbox" }));
    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));

    await waitFor(() =>
      expect(screen.queryByText("このブラウザは音声読み上げに対応していません。")).toBeNull(),
    );
  });
});

describe("[単体] Layout — 承認カードの結線 (#82 / G1 / dialogue-session.mdx §5.9)", () => {
  it("承認要求つきの応答でカードが出て、承認が api まで届き、カードが閉じる", async () => {
    // 無いと何が静かに通るか: api 層と hook の単体テストは互いを知らないので、
    // **hook → Router → SessionScreen の受け渡しを 1 本落としただけ**で承認カードが
    // どの画面にも出なくなる (= #82 の着手前と同じ状態) のに、両方の単体は緑のまま。
    // ブラウザを起動しない層で「画面に出て、押すと api が呼ばれる」までを固定する。
    vi.mocked(startNewConsultation).mockResolvedValue({
      id: "appr-session",
      title: "相談セッション",
      messages: [],
    } as never);
    vi.mocked(sendMessage).mockResolvedValue({
      message: {
        id: "a-appr",
        role: "assistant",
        text: "「send_reply」を実行するには承認が必要です。実行してよろしいですか？",
        createdAt: "2026-01-01",
      },
      approval: {
        id: "appr-1",
        description: "「send_reply」を実行するには承認が必要です。実行してよろしいですか？",
      },
    } as never);
    vi.mocked(respondToApproval).mockResolvedValue({
      id: "a-done",
      role: "assistant",
      text: "[stub] Reply sent to team@example.com.",
      createdAt: "2026-01-01",
    } as never);

    renderLayout();
    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));
    await userEvent.type(
      await screen.findByPlaceholderText("ここに入力 / 話して入力"),
      "この件、返信しておいて",
    );
    await userEvent.click(screen.getByRole("button", { name: "送信" }));

    const card = await screen.findByTestId("approval-request");
    expect(card.textContent).toContain("send_reply");

    await userEvent.click(screen.getByRole("button", { name: "承認して実行" }));

    await waitFor(() => expect(respondToApproval).toHaveBeenCalledWith("appr-1", true));
    await waitFor(() => expect(screen.queryByTestId("approval-request")).toBeNull());
    expect(screen.getByText("[stub] Reply sent to team@example.com.")).toBeTruthy();
  });
});

describe("[L2] Layout — サインアウト時の承認却下 (§5.9 / PR #416 judge major-1)", () => {
  /**
   * 無いと何が静かに通るか: `resetConsultation()` の却下を投げっぱなしにしたまま
   * `msal.logoutRedirect()` を呼ぶ実装。tRPC の httpBatchLink は `mutate()` から
   * **同期に fetch を出さない** (バッチのため 1 tick 遅らせる) ので、リダイレクトが
   * 先に走ると却下は**一度も送信されないまま**消える。`respondToApproval` は
   * 呼ばれているので「呼び出しの有無」だけを見るテストは緑のまま通り、MDX の
   * 「ログアウトでも却下を送る」が実際には成立していない状態が残る。
   *
   * ここで固定するのは**順序** — 却下が決着するまでサインアウトのリダイレクトを
   * 呼ばないこと。
   */
  function signedInDeployedBuild() {
    // 認証有効・非 standalone (= 実デプロイ環境) でしか通らない分岐なので、そこに寄せる。
    vi.stubEnv("DEV", false);
    authState.enabled = true;
    authState.account = { name: "テスト太郎" };
  }

  async function showApprovalCard() {
    vi.mocked(startNewConsultation).mockResolvedValue({
      id: "logout-session",
      title: "相談セッション",
      messages: [],
    } as never);
    vi.mocked(sendMessage).mockResolvedValue({
      message: {
        id: "a-appr",
        role: "assistant",
        text: "「send_reply」を実行するには承認が必要です。実行してよろしいですか？",
        createdAt: "2026-01-01",
      },
      approval: { id: "appr-1", description: "「send_reply」を実行してよろしいですか？" },
    } as never);

    renderLayout();
    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));
    await userEvent.type(
      await screen.findByPlaceholderText("ここに入力 / 話して入力"),
      "この件、返信しておいて",
    );
    await userEvent.click(screen.getByRole("button", { name: "送信" }));
    await screen.findByTestId("approval-request");
  }

  it("承認待ちの却下がサーバに届くまでサインアウトのリダイレクトを呼ばない", async () => {
    signedInDeployedBuild();

    // 却下の応答をこちらで握って「まだ届いていない」状態を作る。
    let settleDiscard: () => void = () => {};
    vi.mocked(respondToApproval).mockImplementation(
      () =>
        new Promise((resolve) => {
          settleDiscard = () =>
            resolve({
              id: "a-cancel",
              role: "assistant",
              text: "操作はキャンセルされました。",
              createdAt: "2026-01-01",
            } as never);
        }),
    );

    await showApprovalCard();

    await userEvent.click(screen.getByRole("button", { name: "アカウント" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "ログアウト" }));

    // 却下は**サーバへ送られている**。
    await waitFor(() => expect(respondToApproval).toHaveBeenCalledWith("appr-1", false));
    // まだ決着していないので、リダイレクトは呼ばれていない (呼ぶと送信前に離脱する)。
    expect(vi.mocked(logout)).not.toHaveBeenCalled();
    // サインアウト開始の宣言は却下より前に済んでいる (トークン取得がサインイン画面に化けない)。
    expect(vi.mocked(disableInteractiveRecovery)).toHaveBeenCalled();

    settleDiscard();

    await waitFor(() => expect(vi.mocked(logout)).toHaveBeenCalled());
  });

  it("承認待ちが無ければ待たずにサインアウトする", async () => {
    // 無いと: 待ち合わせが「常に待つ」に化けても気づけない (通常のログアウトが
    // 上限いっぱい遅れる)。承認要求が無いときは即座にリダイレクトすること。
    signedInDeployedBuild();
    vi.mocked(startNewConsultation).mockResolvedValue({
      id: "logout-session-2",
      title: "相談セッション",
      messages: [],
    } as never);

    renderLayout();
    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));
    await screen.findByPlaceholderText("ここに入力 / 話して入力");

    await userEvent.click(screen.getByRole("button", { name: "アカウント" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "ログアウト" }));

    await waitFor(() => expect(vi.mocked(logout)).toHaveBeenCalled());
    expect(respondToApproval).not.toHaveBeenCalled();
  });
});
