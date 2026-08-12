import * as React from "react";
import {
  Alert,
  AppBar,
  Box,
  Button,
  ButtonBase,
  Container,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Snackbar,
  Stack,
  Toolbar,
  Typography,
} from "@mui/material";
import AccountCircleRoundedIcon from "@mui/icons-material/AccountCircleRounded";
import LogoutRoundedIcon from "@mui/icons-material/LogoutRounded";
import SettingsRoundedIcon from "@mui/icons-material/SettingsRounded";
import { useLocation, useNavigate } from "react-router-dom";
import { authEnabled, getAccount, initAuth, login, logout } from "./auth/msal";
import { useTextToSpeech } from "./voice/useTextToSpeech";
import { useConsultation } from "./consultation/useConsultation";
import { useEnvStatus } from "./envstatus/useEnvStatus";
import { EnvStatusBanner } from "./envstatus/EnvStatusBanner";
import { StubResponseBanner } from "./components/StubResponseBanner";
import { resetStubbedResponse } from "./api/stubStatus";
import { previewSupported } from "./api";
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
  session: "対話セッション",
  settings: "設定",
  paused: "一時保存 / 中断",
  crisisSupport: "危機時サポート",
  extractReview: "抽出結果レビュー",
  problemList: "困りごと一覧",
  problemDetail: "困りごと詳細",
};

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
  const [accountMenuAnchorEl, setAccountMenuAnchorEl] = React.useState<null | HTMLElement>(null);

  // 読み上げ (state 3 + audio ref 5 + VOICEVOX/ブラウザの分岐) は voice/ が所有する (#141)。
  // 音声入力 (STT) は SessionComposer の useVoiceInput が所有する (#121 / ADR 0023)。
  const tts = useTextToSpeech({ standalone, speaker: voicevoxSpeaker });

  // 「今この環境、触って大丈夫?」(env-status-banner.mdx)。VITE_ENV_STATUS_REPO 未設定なら常に unknown。
  const envStatus = useEnvStatus();

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

  // 相談フロー (state 9 + ハンドラ 12) は consultation/ が所有する (#142)。
  const consultation = useConsultation(transition);
  const {
    speakOnce,
    unlock,
    reset: resetTts,
    stop: stopTts,
    clearTransient: clearTtsTransient,
    toggleEnabled: toggleTtsEnabled,
  } = tts;
  const { reset: resetConsultation, startConsultation, sendDraftMessage } = consultation;

  // iOS は最初のユーザージェスチャ内で一度発話しておかないと、以降の自動読み上げが無音になる。
  // 相談フロー (consultation) と読み上げ (tts) は互いを知らないので、「どのタップが音声の
  // 起点になるか」を知っている Layout がここで束ねる (#141 のレビュー指摘: 分離で配線が消えた)。
  const startConsultationWithAudio = React.useCallback(() => {
    // 前セッションの読み上げを新しい相談に持ち込まない (#146 レビュー / dialogue-session.mdx
    // §5.6): 再生中にホームへ戻って開始すると、旧セッションの音声が鳴り続け、メッセージ
    // 0 件の新セッションのマスコットが speaking のまま表示される。開始タップで止める
    // (ttsStatus も idle に戻る)。
    //
    // **順序は stop → unlock 固定**: stop() は speechSynthesis.cancel() を呼ぶので、
    // 逆順だと unlock() が enqueue した無音発話 (iOS / ブラウザ読み上げの解錠) を打ち消す。
    // unlock は解錠済みマーク (ref) で 2 回目以降 no-op になるため、「マークだけ立って
    // 実際は解錠されていない」状態になり、最初の読み上げが無音になりうる。
    stopTts();
    unlock();
    return startConsultation();
  }, [startConsultation, stopTts, unlock]);

  const sendDraftMessageWithAudio = React.useCallback(() => {
    unlock();
    return sendDraftMessage();
  }, [sendDraftMessage, unlock]);

  const toggleTtsEnabledWithAudio = React.useCallback(() => {
    unlock();
    toggleTtsEnabled();
  }, [toggleTtsEnabled, unlock]);

  const handleLogin = React.useCallback(() => {
    if (isDev || authStatus === "authenticated") {
      transition("home");
      return;
    }

    // Entra へリダイレクトしてサインインする (#69)。戻りは main.tsx の initAuth() が回収する。
    void login();
  }, [authStatus, isDev, transition]);

  // 新しいセッションに前セッションの読み上げ表示 (劣化警告 error / 出力経路) を持ち込まない
  // (#146 レビュー): stop() は再生 status しか戻さず、挨拶を撤去した (#241) 新セッションには
  // error を消す契機の speak() も無いため、前セッションの警告が出続けてしまう。
  // **セッション id が変わった時 = start 成功後にだけ**消す (start 失敗時は旧セッションに
  // 留まるので警告も残す — stub バナーのリセットと同じ理由)。enabled 設定は触らない。
  const consultationSessionId = consultation.session?.id ?? null;
  React.useEffect(() => {
    clearTtsTransient();
  }, [consultationSessionId, clearTtsTransient]);

  // AI の返事が増えたら読み上げる。同じ id を二度読まない判定は hook 側 (speakOnce)。
  // hook オブジェクトごと依存にすると毎レンダーで再実行されるため、安定な関数だけを取る。
  React.useEffect(() => {
    const lastAssistantMessage = [...(consultation.session?.messages ?? [])]
      .reverse()
      .find((message) => message.role === "assistant");
    if (!lastAssistantMessage) return;
    speakOnce(lastAssistantMessage.id, lastAssistantMessage.text);
  }, [consultation.session, speakOnce]);

  const handleLogout = React.useCallback(() => {
    resetTts();
    resetConsultation();
    resetStubbedResponse();
    setAuthStatus("anonymous");
    navigate(ROUTE_PATHS.onboarding, { replace: true });
    // standalone（dev / mock デモ）と認証無効ビルドはサインアウト先が無いのでリダイレクトしない。
    if (!standalone && authEnabled) {
      void logout();
    }
  }, [navigate, resetConsultation, resetTts, standalone]);

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

  const isAuthenticated = authStatus === "authenticated";
  const currentRoute = React.useMemo<AppRoute>(() => {
    switch (location.pathname) {
      case ROUTE_PATHS.home:
        return "home";
      case ROUTE_PATHS.specPreview:
        return "specPreview";
      case ROUTE_PATHS.session:
        return "session";
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
    // data-voice-output: 実際に音を出した経路 (dialogue-session.mdx §5.5)。
    // L4 live E2E が「ずんだもんで鳴ったか」を検証するための観測点 (#150)。
    <Box
      data-voice-output={tts.outputMode}
      sx={{ minHeight: "100vh", bgcolor: "background.default" }}
    >
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
              src={`${import.meta.env.BASE_URL}favicon.png`}
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
      <EnvStatusBanner status={envStatus} />
      {/* BFF が stub 応答のときの警告 (#146)。api 層が立てるフラグを購読するだけ。 */}
      <StubResponseBanner />
      {/* 対話画面は 2 ペイン (#187 / ADR 0039) のため広めに取る。他画面は従来の md。 */}
      <Container
        maxWidth={currentRoute === "session" && previewSupported ? "lg" : "md"}
        sx={{ py: 3 }}
      >
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
                loading={consultation.loading}
                session={consultation.session}
                draftMessage={consultation.draftMessage}
                speaking={tts.speaking}
                ttsStatus={tts.status}
                ttsEnabled={tts.enabled}
                voiceError={tts.error}
                extraction={consultation.extraction}
                problems={consultation.problems}
                selectedProblem={consultation.selectedProblem}
                previewEnabled={previewSupported}
                preview={consultation.preview}
                previewStatus={consultation.previewStatus}
                handleRefreshPreview={consultation.refreshPreview}
                themeMode={themeMode}
                onToggleTheme={onToggleTheme}
                transition={transition}
                setDraftMessage={consultation.setDraftMessage}
                handleLogin={handleLogin}
                handleStartConsultation={startConsultationWithAudio}
                handleSendMessage={sendDraftMessageWithAudio}
                toggleTtsEnabled={toggleTtsEnabledWithAudio}
                stopSpeaking={tts.stop}
                handleExtract={consultation.extract}
                handleOpenProblemList={consultation.openProblemList}
                handleOpenProblem={consultation.openProblem}
                handleTriage={consultation.triage}
                handleDismissExtracted={consultation.dismissExtracted}
                handleCreateProblemPlan={consultation.createPlanForProblem}
              />
            </>
          )}
        </Stack>
      </Container>
      <Snackbar
        open={consultation.actionError !== null}
        autoHideDuration={8000}
        onClose={consultation.clearActionError}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert severity="error" variant="filled" onClose={consultation.clearActionError}>
          {consultation.actionError}
        </Alert>
      </Snackbar>
    </Box>
  );
}
