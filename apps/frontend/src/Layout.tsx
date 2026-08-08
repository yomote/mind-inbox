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
  // 履歴の初期読み込みは認証確定後 (#112 / onboarding.mdx)。
  const consultation = useConsultation(transition, {
    ready: authStatus === "authenticated",
  });
  const { speakOnce, reset: resetTts } = tts;
  const { reset: resetConsultation } = consultation;

  const handleLogin = React.useCallback(() => {
    if (isDev || authStatus === "authenticated") {
      transition("home");
      return;
    }

    // Entra へリダイレクトしてサインインする (#69)。戻りは main.tsx の initAuth() が回収する。
    void login();
  }, [authStatus, isDev, transition]);

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
                loading={consultation.loading}
                session={consultation.session}
                draftMessage={consultation.draftMessage}
                speaking={tts.speaking}
                ttsEnabled={tts.enabled}
                voiceError={tts.error}
                result={consultation.result}
                plan={consultation.plan}
                histories={consultation.histories}
                selectedHistory={consultation.selectedHistory}
                extraction={consultation.extraction}
                problems={consultation.problems}
                selectedProblem={consultation.selectedProblem}
                themeMode={themeMode}
                onToggleTheme={onToggleTheme}
                transition={transition}
                setDraftMessage={consultation.setDraftMessage}
                handleLogin={handleLogin}
                handleStartConsultation={consultation.startConsultation}
                handleSendMessage={consultation.sendDraftMessage}
                toggleTtsEnabled={tts.toggleEnabled}
                stopSpeaking={tts.stop}
                handleOrganize={consultation.organize}
                handleExtract={consultation.extract}
                handleCreatePlan={consultation.createPlan}
                handleSaveAndGoHistory={consultation.saveAndGoHistory}
                openHistoryResult={consultation.openHistoryResult}
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
