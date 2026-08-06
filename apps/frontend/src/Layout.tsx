import * as React from "react";
import {
  AppBar,
  Box,
  Button,
  ButtonBase,
  Container,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Toolbar,
  Typography,
} from "@mui/material";
import AccountCircleRoundedIcon from "@mui/icons-material/AccountCircleRounded";
import LogoutRoundedIcon from "@mui/icons-material/LogoutRounded";
import SettingsRoundedIcon from "@mui/icons-material/SettingsRounded";
import { useLocation, useNavigate } from "react-router-dom";
import { authEnabled, getAccount, initAuth, login, logout } from "./auth/msal";
import {
  createActionPlan,
  createProblemPlan,
  extractMentions,
  loadHistories,
  loadProblem,
  loadProblems,
  organizeResult,
  saveHistory,
  sendMessage,
  startNewConsultation,
  triageProblem,
} from "./api";
import type {
  ActionPlan,
  ConsultationSession,
  ExtractionResult,
  HistoryItem,
  OrganizedResult,
  Problem,
  TriageInput,
} from "./api";
import type { PaletteMode } from "@mui/material";
import { AppRouter, ROUTE_PATHS } from "./Router";
import type { AppRoute, AuthStatus } from "./Router";

const DevSpecMdxPreview = import.meta.env.DEV
  ? React.lazy(() =>
      import("./spec/DevSpecMdxPreview").then((m) => ({
        default: m.DevSpecMdxPreview,
      })),
    )
  : null;

type LayoutProps = {
  themeMode: PaletteMode;
  onToggleTheme: () => void;
};

const HEADER_BY_ROUTE: Record<AppRoute, string> = {
  onboarding: "起動画面 / オンボーディング",
  home: "ホーム",
  specPreview: "UI仕様プレビュー",
  newConsultation: "新しい相談を始める",
  session: "対話セッション",
  result: "整理結果",
  actionPlan: "行動プラン / 保存",
  history: "履歴・振り返り",
  settings: "設定",
  paused: "一時保存 / 中断",
  crisisSupport: "危機時サポート",
  extractReview: "抽出結果レビュー",
  problemList: "困りごと一覧",
  problemDetail: "困りごと詳細",
};

/**
 * 最初の発話からセッションのタイトルを自動生成する（mock の簡易 AI タイトル）。
 * タイトルは最初に聞かず、内容から後付けする方針。実 AI 生成は Phase A で差し替え。
 */
function deriveSessionTitle(text: string): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  if (!oneLine) return "新しい相談";
  return oneLine.length > 18 ? `${oneLine.slice(0, 18)}…` : oneLine;
}

export function Layout({ themeMode, onToggleTheme }: LayoutProps) {
  const isDev = import.meta.env.DEV;
  // mock モード（VITE_USE_MOCK=true）は BFF も認証も無い自己完結デモ。
  // 認証ゲートと login/logout をスキップして触れるようにする。
  const useMock = import.meta.env.VITE_USE_MOCK === "true";
  const standalone = isDev || useMock;
  const location = useLocation();
  const navigate = useNavigate();
  const voicevoxSpeaker = Number(import.meta.env.VITE_VOICEVOX_SPEAKER || "3");

  const [authStatus, setAuthStatus] = React.useState<AuthStatus>("loading");
  const [loading, setLoading] = React.useState(false);
  const [listening, setListening] = React.useState(false);
  const [interimTranscript, setInterimTranscript] = React.useState("");
  const [voiceError, setVoiceError] = React.useState<string | null>(null);
  const [speaking, setSpeaking] = React.useState(false);
  const [ttsEnabled, setTtsEnabled] = React.useState(true);

  const [concern, setConcern] = React.useState("");
  const [draftMessage, setDraftMessage] = React.useState("");

  const [session, setSession] = React.useState<ConsultationSession | null>(null);
  const [result, setResult] = React.useState<OrganizedResult | null>(null);
  const [plan, setPlan] = React.useState<ActionPlan | null>(null);
  const [histories, setHistories] = React.useState<HistoryItem[]>([]);

  const [selectedHistory, setSelectedHistory] = React.useState<HistoryItem | null>(null);
  const [extraction, setExtraction] = React.useState<ExtractionResult | null>(null);
  const [problems, setProblems] = React.useState<Problem[]>([]);
  const [selectedProblem, setSelectedProblem] = React.useState<Problem | null>(null);
  const [accountMenuAnchorEl, setAccountMenuAnchorEl] = React.useState<null | HTMLElement>(null);

  const recognitionRef = React.useRef<SpeechRecognition | null>(null);
  // ユーザーが「聞き続けてほしい」状態か（沈黙で勝手に止まったら再開する判定に使う）。
  const shouldListenRef = React.useRef(false);
  // 認識エンジンが実際に走っているか（二重 start による InvalidStateError を防ぐ）。
  const recognitionRunningRef = React.useRef(false);
  const activeAudioRef = React.useRef<HTMLAudioElement | null>(null);
  const activeAudioUrlRef = React.useRef<string | null>(null);
  const lastSpokenAssistantMessageIdRef = React.useRef<string | null>(null);
  const voiceCacheRef = React.useRef<Map<string, Blob>>(new Map());
  // iOS は最初のタップ起点でしか音を出せないため、一度ジェスチャ内で「解錠」しておく。
  const audioUnlockedRef = React.useRef(false);

  const speechRecognitionCtor = React.useMemo<SpeechRecognitionConstructor | undefined>(() => {
    if (typeof window === "undefined") return undefined;
    return window.SpeechRecognition || window.webkitSpeechRecognition;
  }, []);

  const sttSupported = Boolean(speechRecognitionCtor);

  React.useEffect(() => {
    void (async () => {
      const data = await loadHistories();
      setHistories(data);
    })();
  }, []);

  React.useEffect(() => {
    let active = true;

    if (standalone) {
      setAuthStatus("authenticated");
      return () => {
        active = false;
      };
    }

    // 認証が構成されていないビルド（VITE_ENTRA_* 未設定）では門が無いので通す。
    // API 側も同時に EasyAuth 未構成のため、UI だけ閉じても意味がない
    // （公開 URL に出す場合は deploy-frontend.sh が警告する）。
    if (!authEnabled) {
      setAuthStatus("authenticated");
      return () => {
        active = false;
      };
    }

    // 認可の門は Functions(EasyAuth) 側にある (#69)。UI はここで
    // 「Entra のサインイン済みアカウントがあるか」だけを見る。
    void (async () => {
      try {
        await initAuth();
        if (!active) return;
        setAuthStatus(getAccount() ? "authenticated" : "anonymous");
      } catch {
        if (!active) return;
        setAuthStatus("anonymous");
      }
    })();

    return () => {
      active = false;
    };
  }, [standalone]);

  const transition = React.useCallback(
    (next: AppRoute) => {
      if (authStatus !== "authenticated" && next !== "onboarding") {
        navigate(ROUTE_PATHS.onboarding, { replace: true });
        return;
      }
      if (next === "specPreview" && !isDev) {
        navigate(ROUTE_PATHS.home, { replace: true });
        return;
      }

      navigate(ROUTE_PATHS[next]);
    },
    [authStatus, isDev, navigate],
  );

  const handleLogin = React.useCallback(() => {
    if (isDev || authStatus === "authenticated") {
      transition("home");
      return;
    }

    // Entra へリダイレクトしてサインインする (#69)。戻りは main.tsx の initAuth() が回収する。
    void login();
  }, [authStatus, isDev, transition]);

  const stopSpeaking = React.useCallback(() => {
    if (activeAudioRef.current) {
      activeAudioRef.current.pause();
      activeAudioRef.current.currentTime = 0;
      activeAudioRef.current = null;
    }

    if (activeAudioUrlRef.current) {
      URL.revokeObjectURL(activeAudioUrlRef.current);
      activeAudioUrlRef.current = null;
    }

    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }

    setSpeaking(false);
  }, []);

  const synthesizeWithVoicevox = React.useCallback(
    async (text: string): Promise<Blob> => {
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, speaker: voicevoxSpeaker }),
      });

      if (res.status === 204) {
        // VOICEVOX_BASE_URL 未設定時の stub。フォールバックをトリガーする。
        throw new Error("TTS_STUB");
      }

      if (!res.ok) {
        throw new Error(`TTS synthesis failed: ${res.status}`);
      }

      return await res.blob();
    },
    [voicevoxSpeaker],
  );

  // ブラウザ内蔵の音声合成で読み上げる（VOICEVOX が無い時のフォールバック / mock デモ用）。
  const speakWithBrowser = React.useCallback((text: string) => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setSpeaking(false);
      setVoiceError("このブラウザは音声読み上げに対応していません。");
      return;
    }

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "ja-JP";
    // 日本語ボイスがあれば優先（無ければ既定ボイス + lang ヒント）。
    const jaVoice = window.speechSynthesis
      .getVoices()
      .find((v) => v.lang?.toLowerCase().startsWith("ja"));
    if (jaVoice) utterance.voice = jaVoice;
    utterance.rate = 1;
    setSpeaking(true);
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utterance);
  }, []);

  // ユーザー操作（タップ）の中で一度だけ音声出力を解錠する。
  // iOS は最初のジェスチャ内で発話/再生しておかないと、以降の自動読み上げが無音になる。
  const unlockAudioPlayback = React.useCallback(() => {
    if (audioUnlockedRef.current) return;
    audioUnlockedRef.current = true;
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
    try {
      const u = new SpeechSynthesisUtterance(" ");
      u.volume = 0;
      window.speechSynthesis.speak(u);
    } catch {
      // 解錠失敗は致命ではない。
    }
  }, []);

  const speakText = React.useCallback(
    async (text: string) => {
      if (!ttsEnabled || !text.trim()) return;

      setVoiceError(null);
      stopSpeaking();

      // standalone（mock デモ）は BFF/VOICEVOX が無いので、ネットワークを叩かず
      // ブラウザ内蔵 TTS で直接読み上げる（/api/tts の 404 待ちで詰まらせない）。
      if (standalone) {
        speakWithBrowser(text);
        return;
      }

      setSpeaking(true);

      try {
        const cacheKey = `${voicevoxSpeaker}:${text}`;
        const audioBlob =
          voiceCacheRef.current.get(cacheKey) || (await synthesizeWithVoicevox(text));

        if (!voiceCacheRef.current.has(cacheKey)) {
          voiceCacheRef.current.set(cacheKey, audioBlob);
          if (voiceCacheRef.current.size > 30) {
            const oldest = voiceCacheRef.current.keys().next().value;
            if (oldest) {
              voiceCacheRef.current.delete(oldest);
            }
          }
        }

        const objectUrl = URL.createObjectURL(audioBlob);
        activeAudioUrlRef.current = objectUrl;

        const audio = new Audio(objectUrl);
        activeAudioRef.current = audio;
        audio.onended = () => {
          setSpeaking(false);
          if (activeAudioUrlRef.current) {
            URL.revokeObjectURL(activeAudioUrlRef.current);
            activeAudioUrlRef.current = null;
          }
          activeAudioRef.current = null;
        };
        audio.onerror = () => {
          setSpeaking(false);
          setVoiceError("音声の再生に失敗しました。");
        };

        await audio.play();
      } catch {
        // VOICEVOX 失敗時はブラウザ TTS にフォールバック。
        speakWithBrowser(text);
      }
    },
    [
      speakWithBrowser,
      standalone,
      stopSpeaking,
      synthesizeWithVoicevox,
      ttsEnabled,
      voicevoxSpeaker,
    ],
  );

  const stopListening = React.useCallback(() => {
    // 意図を先に落とす → onend が再開しないようにしてから停止。
    shouldListenRef.current = false;
    setListening(false);
    setInterimTranscript("");
    const recognition = recognitionRef.current;
    if (!recognition) return;
    try {
      recognition.stop();
    } catch {
      // 既に停止済みなどは無視。
    }
  }, []);

  const startListening = React.useCallback(() => {
    if (!speechRecognitionCtor) return;

    setVoiceError(null);
    shouldListenRef.current = true;

    if (!recognitionRef.current) {
      const recognition = new speechRecognitionCtor();
      recognition.lang = "ja-JP";
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;

      recognition.onstart = () => {
        recognitionRunningRef.current = true;
        setListening(true);
      };

      recognition.onresult = (event: SpeechRecognitionEvent) => {
        let finalText = "";
        let interimText = "";

        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const result = event.results[i];
          const transcript = result[0]?.transcript ?? "";
          if (result.isFinal) {
            finalText += transcript;
          } else {
            interimText += transcript;
          }
        }

        setInterimTranscript(interimText.trim());
        if (finalText.trim()) {
          setDraftMessage((prev) => {
            const separator = prev.trim().length > 0 ? "\n" : "";
            return `${prev}${separator}${finalText.trim()}`;
          });
        }
      };

      recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
        const err = event.error;
        // continuous モードでは沈黙や中断で頻繁に出る。無害なので握り潰し、
        // 継続は onend → 自動再開に任せる（「エラー表示で固まった」体験を防ぐ）。
        if (err === "no-speech" || err === "aborted") {
          return;
        }
        // マイク権限・デバイス起因は復帰不能なので意図を落として明示する。
        if (err === "not-allowed" || err === "service-not-allowed" || err === "audio-capture") {
          shouldListenRef.current = false;
          setVoiceError("マイクを使えませんでした。ブラウザのマイク許可を確認してください。");
          return;
        }
        setVoiceError(`音声認識エラー: ${err}`);
      };

      recognition.onend = () => {
        recognitionRunningRef.current = false;
        setInterimTranscript("");
        // 継続意図があるのに止まった（沈黙タイムアウト等）→ 自動再開。
        // sync 再開は InvalidStateError を起こしやすいので次 tick で。
        if (shouldListenRef.current) {
          window.setTimeout(() => {
            if (!shouldListenRef.current || recognitionRunningRef.current) return;
            try {
              recognition.start();
            } catch {
              // まだ停止しきっていない場合などは次の onend で再試行される。
            }
          }, 150);
          return;
        }
        setListening(false);
      };

      recognitionRef.current = recognition;
    }

    if (!recognitionRunningRef.current) {
      try {
        recognitionRef.current.start();
      } catch {
        // 既に開始中なら無視（onstart で listening は同期される）。
      }
    }
    setListening(true);
  }, [speechRecognitionCtor]);

  const toggleListening = React.useCallback(() => {
    unlockAudioPlayback();
    if (listening) {
      stopListening();
      return;
    }
    startListening();
  }, [listening, startListening, stopListening, unlockAudioPlayback]);

  const toggleTtsEnabled = React.useCallback(() => {
    unlockAudioPlayback();
    setTtsEnabled((prev) => {
      const next = !prev;
      if (!next) {
        stopSpeaking();
      }
      return next;
    });
  }, [stopSpeaking, unlockAudioPlayback]);

  React.useEffect(() => {
    return () => {
      stopListening();
      stopSpeaking();
    };
  }, [stopListening, stopSpeaking]);

  const handleStartConsultation = async () => {
    unlockAudioPlayback(); // タップ起点で音声を解錠（iOS の自動再生ブロック対策）
    setLoading(true);
    try {
      const newSession = await startNewConsultation(concern.trim());
      setSession(newSession);
      setResult(null);
      setPlan(null);
      setSelectedHistory(null);
      transition("session");
    } finally {
      setLoading(false);
    }
  };

  const handleSendMessage = async () => {
    if (!session || !draftMessage.trim() || loading) return;

    unlockAudioPlayback(); // タップ起点で音声を解錠（iOS の自動再生ブロック対策）

    const userMessage = {
      id: `u-${Date.now()}`,
      role: "user" as const,
      text: draftMessage.trim(),
      createdAt: new Date().toISOString(),
    };

    setDraftMessage("");
    // 最初のユーザー発話でタイトルを内容から自動生成（ChatGPT 風: 開始時は聞かない）。
    const isFirstUserMessage = !session.messages.some((m) => m.role === "user");
    const nextTitle = isFirstUserMessage ? deriveSessionTitle(userMessage.text) : session.title;
    const nextMessages = [...session.messages, userMessage];
    setSession({ ...session, title: nextTitle, messages: nextMessages });

    setLoading(true);
    try {
      const assistantMessage = await sendMessage(session.id, userMessage.text);
      setSession((prev) =>
        prev ? { ...prev, messages: [...prev.messages, assistantMessage] } : prev,
      );
    } finally {
      setLoading(false);
    }
  };

  React.useEffect(() => {
    if (!session || !ttsEnabled) return;

    const lastAssistantMessage = [...session.messages]
      .reverse()
      .find((message) => message.role === "assistant");

    if (!lastAssistantMessage) return;
    if (lastSpokenAssistantMessageIdRef.current === lastAssistantMessage.id) {
      return;
    }

    lastSpokenAssistantMessageIdRef.current = lastAssistantMessage.id;
    void speakText(lastAssistantMessage.text);
  }, [session, speakText, ttsEnabled]);

  const handleOrganize = async () => {
    if (!session || loading) return;
    setLoading(true);
    try {
      const organized = await organizeResult(session.id);
      setResult(organized);
      transition("result");
    } finally {
      setLoading(false);
    }
  };

  const handleExtract = async () => {
    if (!session || loading) return;
    setLoading(true);
    try {
      const res = await extractMentions(session.id);
      setExtraction(res);
      transition("extractReview");
    } finally {
      setLoading(false);
    }
  };

  const handleOpenProblemList = async () => {
    setLoading(true);
    try {
      const list = await loadProblems();
      setProblems(list);
      transition("problemList");
    } finally {
      setLoading(false);
    }
  };

  const handleOpenProblem = async (id: string) => {
    setLoading(true);
    try {
      const found = await loadProblem(id);
      if (found) {
        setSelectedProblem(found);
        transition("problemDetail");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleTriage = async (input: TriageInput) => {
    if (loading) return;
    setLoading(true);
    try {
      const updated = await triageProblem(input);
      setSelectedProblem(updated);
      // 一覧キャッシュを最新化（棚卸し / 却下 / 統合がそのまま反映されるように）。
      setProblems(await loadProblems());
      if (input.action === "dismiss" || input.action === "merge") {
        // 対象が消えたので一覧へ戻す。
        transition("problemList");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleDismissExtracted = async (problemId: string) => {
    if (loading) return;
    setLoading(true);
    try {
      await triageProblem({ action: "dismiss", problemId });
      setExtraction((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.filter((item) => item.grouping.problemId !== problemId),
              newProblemCount: Math.max(0, prev.newProblemCount - 1),
            }
          : prev,
      );
    } finally {
      setLoading(false);
    }
  };

  const handleCreateProblemPlan = async (problemId: string) => {
    if (loading) return;
    setLoading(true);
    try {
      const updated = await createProblemPlan(problemId);
      if (updated) setSelectedProblem(updated);
    } finally {
      setLoading(false);
    }
  };

  const handleCreatePlan = async () => {
    if (!result || loading) return;
    setLoading(true);
    try {
      const nextPlan = await createActionPlan(result);
      setPlan(nextPlan);
      transition("actionPlan");
    } finally {
      setLoading(false);
    }
  };

  const handleSaveAndGoHistory = async () => {
    if (!result || !plan || !session) return;

    setLoading(true);
    try {
      const item = await saveHistory({
        sessionId: session.id,
        title: session.title || "相談履歴",
        result,
        plan,
      });

      setHistories((prev) => [item, ...prev]);
      setSelectedHistory(item);
      transition("history");
    } finally {
      setLoading(false);
    }
  };

  const openHistoryResult = (item: HistoryItem) => {
    setSelectedHistory(item);
    setResult(item.result);
    transition("result");
  };

  const isAccountMenuOpen = Boolean(accountMenuAnchorEl);

  const handleOpenAccountMenu = (event: React.MouseEvent<HTMLElement>) => {
    setAccountMenuAnchorEl(event.currentTarget);
  };

  const handleCloseAccountMenu = () => {
    setAccountMenuAnchorEl(null);
  };

  const handleOpenSettingsFromMenu = () => {
    handleCloseAccountMenu();
    transition("settings");
  };

  const handleLogoutFromMenu = () => {
    handleCloseAccountMenu();
    handleLogout();
  };

  const handleLogout = React.useCallback(() => {
    stopListening();
    stopSpeaking();
    setAuthStatus("anonymous");
    setLoading(false);
    setListening(false);
    setInterimTranscript("");
    setVoiceError(null);
    setTtsEnabled(true);
    setConcern("");
    setDraftMessage("");
    setSession(null);
    setResult(null);
    setPlan(null);
    setSelectedHistory(null);
    setExtraction(null);
    setProblems([]);
    setSelectedProblem(null);
    lastSpokenAssistantMessageIdRef.current = null;
    voiceCacheRef.current.clear();
    navigate(ROUTE_PATHS.onboarding, { replace: true });
    // standalone（dev / mock デモ）と認証無効ビルドはサインアウト先が無いのでリダイレクトしない。
    if (!standalone && authEnabled) {
      void logout();
    }
  }, [navigate, standalone, stopListening, stopSpeaking]);

  const isAuthenticated = authStatus === "authenticated";
  const currentRoute = React.useMemo<AppRoute>(() => {
    switch (location.pathname) {
      case ROUTE_PATHS.home:
        return "home";
      case ROUTE_PATHS.specPreview:
        return "specPreview";
      case ROUTE_PATHS.newConsultation:
        return "newConsultation";
      case ROUTE_PATHS.session:
        return "session";
      case ROUTE_PATHS.result:
        return "result";
      case ROUTE_PATHS.actionPlan:
        return "actionPlan";
      case ROUTE_PATHS.history:
        return "history";
      case ROUTE_PATHS.settings:
        return "settings";
      case ROUTE_PATHS.paused:
        return "paused";
      case ROUTE_PATHS.crisisSupport:
        return "crisisSupport";
      case ROUTE_PATHS.extractReview:
        return "extractReview";
      case ROUTE_PATHS.problemList:
        return "problemList";
      case ROUTE_PATHS.problemDetail:
        return "problemDetail";
      case ROUTE_PATHS.onboarding:
      default:
        return "onboarding";
    }
  }, [location.pathname]);

  const activeHeader =
    isAuthenticated && currentRoute !== "onboarding" ? currentRoute : "onboarding";

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "background.default" }}>
      <AppBar
        position="fixed"
        elevation={0}
        sx={(t) => ({
          bgcolor: "background.paper",
          color: "text.primary",
          borderBottom: `1px solid ${t.palette.divider}`,
        })}
      >
        <Toolbar sx={{ gap: 1.5 }}>
          <ButtonBase
            onClick={() => transition(isAuthenticated ? "home" : "onboarding")}
            sx={{
              flex: 1,
              justifyContent: "flex-start",
              display: "inline-flex",
              alignItems: "center",
              gap: 1,
              borderRadius: 1,
              p: 0.5,
            }}
          >
            <Box
              component="img"
              src={`${import.meta.env.BASE_URL}fabicon.png`}
              alt=""
              sx={{ width: 28, height: 28, borderRadius: 1 }}
            />
            <Typography variant="h6" fontWeight={800}>
              Mind Inbox
            </Typography>
          </ButtonBase>
          {isAuthenticated && currentRoute !== "onboarding" && (
            <Button
              variant="text"
              startIcon={<AccountCircleRoundedIcon />}
              onClick={handleOpenAccountMenu}
            >
              アカウント
            </Button>
          )}
          <Menu
            anchorEl={accountMenuAnchorEl}
            open={isAccountMenuOpen}
            onClose={handleCloseAccountMenu}
            anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
            transformOrigin={{ vertical: "top", horizontal: "right" }}
          >
            <MenuItem onClick={handleOpenSettingsFromMenu}>
              <ListItemIcon>
                <SettingsRoundedIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText>設定</ListItemText>
            </MenuItem>
            <MenuItem onClick={handleLogoutFromMenu}>
              <ListItemIcon>
                <LogoutRoundedIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText>ログアウト</ListItemText>
            </MenuItem>
          </Menu>
        </Toolbar>
      </AppBar>

      <Toolbar />
      <Container maxWidth="md" sx={{ py: 3 }}>
        <Stack spacing={2}>
          {authStatus === "loading" ? (
            <Typography color="text.secondary">認証状態を確認中...</Typography>
          ) : (
            <>
              <Typography variant="h5" fontWeight={800}>
                {HEADER_BY_ROUTE[activeHeader]}
              </Typography>
              <AppRouter
                authStatus={authStatus}
                isAuthenticated={isAuthenticated}
                isDev={isDev}
                DevSpecMdxPreview={DevSpecMdxPreview}
                concern={concern}
                loading={loading}
                session={session}
                draftMessage={draftMessage}
                sttSupported={sttSupported}
                listening={listening}
                interimTranscript={interimTranscript}
                speaking={speaking}
                ttsEnabled={ttsEnabled}
                voiceError={voiceError}
                result={result}
                plan={plan}
                histories={histories}
                selectedHistory={selectedHistory}
                extraction={extraction}
                problems={problems}
                selectedProblem={selectedProblem}
                themeMode={themeMode}
                onToggleTheme={onToggleTheme}
                transition={transition}
                setConcern={setConcern}
                setDraftMessage={setDraftMessage}
                handleLogin={handleLogin}
                handleStartConsultation={handleStartConsultation}
                handleSendMessage={handleSendMessage}
                toggleListening={toggleListening}
                toggleTtsEnabled={toggleTtsEnabled}
                stopSpeaking={stopSpeaking}
                handleOrganize={handleOrganize}
                handleExtract={handleExtract}
                handleCreatePlan={handleCreatePlan}
                handleSaveAndGoHistory={handleSaveAndGoHistory}
                openHistoryResult={openHistoryResult}
                handleOpenProblemList={handleOpenProblemList}
                handleOpenProblem={handleOpenProblem}
                handleTriage={handleTriage}
                handleDismissExtracted={handleDismissExtracted}
                handleCreateProblemPlan={handleCreateProblemPlan}
              />
            </>
          )}
        </Stack>
      </Container>
    </Box>
  );
}
