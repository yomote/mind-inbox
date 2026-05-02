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
import {
  createActionPlan,
  loadHistories,
  organizeResult,
  saveHistory,
  sendMessage,
  startNewConsultation,
} from "./api";
import type { ActionPlan, ConsultationSession, HistoryItem, OrganizedResult } from "./api";
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

type StaticWebAppsClientPrincipal = {
  userDetails?: string;
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
};

function getClientPrincipal(payload: unknown): StaticWebAppsClientPrincipal | null {
  if (Array.isArray(payload)) {
    const [entry] = payload;
    if (
      entry &&
      typeof entry === "object" &&
      "clientPrincipal" in entry &&
      entry.clientPrincipal &&
      typeof entry.clientPrincipal === "object"
    ) {
      return entry.clientPrincipal as StaticWebAppsClientPrincipal;
    }

    return null;
  }

  if (
    payload &&
    typeof payload === "object" &&
    "clientPrincipal" in payload &&
    payload.clientPrincipal &&
    typeof payload.clientPrincipal === "object"
  ) {
    return payload.clientPrincipal as StaticWebAppsClientPrincipal;
  }

  return null;
}

export function Layout({ themeMode, onToggleTheme }: LayoutProps) {
  const isDev = import.meta.env.DEV;
  const location = useLocation();
  const navigate = useNavigate();
  const voicevoxSpeaker = Number(import.meta.env.VITE_VOICEVOX_SPEAKER || "3");
  const loginUrl = "/login";
  const logoutUrl = "/logout";

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
  const [accountMenuAnchorEl, setAccountMenuAnchorEl] = React.useState<null | HTMLElement>(null);

  const recognitionRef = React.useRef<SpeechRecognition | null>(null);
  const activeAudioRef = React.useRef<HTMLAudioElement | null>(null);
  const activeAudioUrlRef = React.useRef<string | null>(null);
  const lastSpokenAssistantMessageIdRef = React.useRef<string | null>(null);
  const voiceCacheRef = React.useRef<Map<string, Blob>>(new Map());

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

    if (isDev) {
      setAuthStatus("authenticated");
      return () => {
        active = false;
      };
    }

    void (async () => {
      try {
        const response = await fetch("/.auth/me", {
          credentials: "same-origin",
          cache: "no-store",
        });

        if (!response.ok) {
          throw new Error("Failed to load auth state");
        }

        const auth = (await response.json()) as unknown;
        const clientPrincipal = getClientPrincipal(auth);
        const authenticated = Boolean(clientPrincipal?.userDetails);

        if (!active) return;

        setAuthStatus(authenticated ? "authenticated" : "anonymous");
      } catch {
        if (!active) return;
        setAuthStatus("anonymous");
      }
    })();

    return () => {
      active = false;
    };
  }, [isDev]);

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

    const postLoginRedirectUri = encodeURIComponent(`${window.location.origin}${ROUTE_PATHS.home}`);

    window.location.assign(`${loginUrl}?post_login_redirect_uri=${postLoginRedirectUri}`);
  }, [authStatus, isDev, loginUrl, transition]);

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

  const speakText = React.useCallback(
    async (text: string) => {
      if (!ttsEnabled || !text.trim()) return;

      setVoiceError(null);
      stopSpeaking();
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
        if (typeof window === "undefined" || !("speechSynthesis" in window)) {
          setSpeaking(false);
          setVoiceError("音声合成に失敗しました。VOICEVOX接続を確認してください。");
          return;
        }

        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = "ja-JP";
        utterance.rate = 1;
        utterance.onend = () => setSpeaking(false);
        utterance.onerror = () => {
          setSpeaking(false);
          setVoiceError("音声合成に失敗しました。VOICEVOX接続を確認してください。");
        };

        window.speechSynthesis.speak(utterance);
      }
    },
    [stopSpeaking, synthesizeWithVoicevox, ttsEnabled, voicevoxSpeaker],
  );

  const stopListening = React.useCallback(() => {
    const recognition = recognitionRef.current;
    if (!recognition) return;
    recognition.stop();
  }, []);

  const startListening = React.useCallback(() => {
    if (!speechRecognitionCtor || loading) return;

    setVoiceError(null);

    if (!recognitionRef.current) {
      const recognition = new speechRecognitionCtor();
      recognition.lang = "ja-JP";
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;

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
        setVoiceError(`音声認識エラー: ${event.error}`);
      };

      recognition.onend = () => {
        setListening(false);
        setInterimTranscript("");
      };

      recognitionRef.current = recognition;
    }

    recognitionRef.current.start();
    setListening(true);
  }, [loading, speechRecognitionCtor]);

  const toggleListening = React.useCallback(() => {
    if (listening) {
      stopListening();
      return;
    }
    startListening();
  }, [listening, startListening, stopListening]);

  const toggleTtsEnabled = React.useCallback(() => {
    setTtsEnabled((prev) => {
      const next = !prev;
      if (!next) {
        stopSpeaking();
      }
      return next;
    });
  }, [stopSpeaking]);

  React.useEffect(() => {
    return () => {
      stopListening();
      stopSpeaking();
    };
  }, [stopListening, stopSpeaking]);

  const handleStartConsultation = async () => {
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

    const userMessage = {
      id: `u-${Date.now()}`,
      role: "user" as const,
      text: draftMessage.trim(),
      createdAt: new Date().toISOString(),
    };

    setDraftMessage("");
    const nextMessages = [...session.messages, userMessage];
    setSession({ ...session, messages: nextMessages });

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
    lastSpokenAssistantMessageIdRef.current = null;
    voiceCacheRef.current.clear();
    navigate(ROUTE_PATHS.onboarding, { replace: true });
    window.location.assign(logoutUrl);
  }, [logoutUrl, navigate, stopListening, stopSpeaking]);

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
              src="/fabicon.png"
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
                handleCreatePlan={handleCreatePlan}
                handleSaveAndGoHistory={handleSaveAndGoHistory}
                openHistoryResult={openHistoryResult}
              />
            </>
          )}
        </Stack>
      </Container>
    </Box>
  );
}
