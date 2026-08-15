import * as React from "react";
import { Button, Stack, Typography } from "@mui/material";
import { Navigate, Route, Routes } from "react-router-dom";
import type { PaletteMode } from "@mui/material";
import type {
  ApprovalRequest,
  ConsultationSession,
  ExtractionResult,
  Problem,
  TriageInput,
} from "./api";
import type { TtsStatus } from "./voice/useTextToSpeech";
import { SessionScreen } from "./components/session/SessionScreen";
import { OnboardingScreen } from "./components/screens/OnboardingScreen";
import { HomeScreen } from "./components/screens/HomeScreen";
import { PausedScreen } from "./components/screens/PausedScreen";
import { CrisisSupportScreen } from "./components/screens/CrisisSupportScreen";
import { SettingsScreen } from "./components/screens/SettingsScreen";
import { ExtractReviewScreen } from "./components/screens/ExtractReviewScreen";
import { ProblemListScreen } from "./components/screens/ProblemListScreen";
import { ProblemDetailScreen } from "./components/screens/ProblemDetailScreen";

export type AuthStatus = "loading" | "authenticated" | "anonymous";

export const ROUTE_PATHS = {
  onboarding: "/",
  home: "/home",
  specPreview: "/spec",
  session: "/consultations/current",
  settings: "/settings",
  paused: "/consultations/current/paused",
  crisisSupport: "/consultations/current/crisis-support",
  extractReview: "/consultations/current/extract",
  problemList: "/problems",
  problemDetail: "/problems/current",
} as const;

export type AppRoute = keyof typeof ROUTE_PATHS;

type AppRouterProps = {
  authStatus: AuthStatus;
  isAuthenticated: boolean;
  isDev: boolean;
  DevSpecMdxPreview: React.LazyExoticComponent<React.ComponentType<unknown>> | null;
  loading: boolean;
  session: ConsultationSession | null;
  draftMessage: string;
  speaking: boolean;
  ttsStatus?: TtsStatus;
  ttsEnabled: boolean;
  voiceError: string | null;
  extraction: ExtractionResult | null;
  problems: Problem[];
  selectedProblem: Problem | null;
  /** 2 ペイン下書きプレビュー (#187 / ADR 0039)。 */
  previewEnabled: boolean;
  preview: ExtractionResult | null;
  previewStatus: "idle" | "updating" | "error";
  handleRefreshPreview: () => void;
  /** 副作用ツールの承認待ち (#82 / G1 / dialogue-session.mdx §5.9)。 */
  pendingApproval: ApprovalRequest | null;
  handleRespondToApproval: (approved: boolean) => void;
  /** AI が提示した選択肢 (#432-b / dialogue-session.mdx §5.10)。空 = 選択肢なし。 */
  offeredChoices: string[];
  /** 選択肢をタップする = その文言を次の発話として送る。 */
  handleSelectChoice: (choice: string) => void;
  themeMode: PaletteMode;
  onToggleTheme: () => void;
  /** 読み上げ速度 (等倍 = 1.0 / #242)。設定画面で変える。 */
  speedScale: number;
  onChangeSpeedScale: (value: number) => void;
  transition: (next: AppRoute) => void;
  setDraftMessage: (value: string) => void;
  handleLogin: () => void;
  handleStartConsultation: () => Promise<void>;
  handleSendMessage: () => Promise<void>;
  toggleTtsEnabled: () => void;
  stopSpeaking: () => void;
  handleExtract: () => Promise<void>;
  handleOpenProblemList: () => Promise<void>;
  handleOpenProblem: (id: string) => Promise<void>;
  handleTriage: (input: TriageInput) => Promise<void>;
  handleDismissExtracted: (problemId: string) => Promise<void>;
  handleCreateProblemPlan: (problemId: string) => Promise<void>;
};

function ProtectedRoute({
  authStatus,
  children,
}: {
  authStatus: AuthStatus;
  children: React.ReactNode;
}) {
  if (authStatus !== "authenticated") {
    return <Navigate to={ROUTE_PATHS.onboarding} replace />;
  }

  return <>{children}</>;
}

function RouteStateGuard({
  when,
  redirectTo,
  children,
}: {
  when: boolean;
  redirectTo: string;
  children: React.ReactNode;
}) {
  if (!when) {
    return <Navigate to={redirectTo} replace />;
  }

  return <>{children}</>;
}

export function AppRouter({
  authStatus,
  isAuthenticated,
  isDev,
  DevSpecMdxPreview,
  loading,
  session,
  draftMessage,
  speaking,
  ttsStatus,
  ttsEnabled,
  voiceError,
  extraction,
  problems,
  selectedProblem,
  previewEnabled,
  preview,
  previewStatus,
  handleRefreshPreview,
  pendingApproval,
  handleRespondToApproval,
  offeredChoices,
  handleSelectChoice,
  themeMode,
  onToggleTheme,
  speedScale,
  onChangeSpeedScale,
  transition,
  setDraftMessage,
  handleLogin,
  handleStartConsultation,
  handleSendMessage,
  toggleTtsEnabled,
  stopSpeaking,
  handleExtract,
  handleOpenProblemList,
  handleOpenProblem,
  handleTriage,
  handleDismissExtracted,
  handleCreateProblemPlan,
}: AppRouterProps) {
  return (
    <Routes>
      <Route
        path={ROUTE_PATHS.onboarding}
        element={
          isAuthenticated ? (
            <Navigate to={ROUTE_PATHS.home} replace />
          ) : (
            <OnboardingScreen onStart={handleLogin} />
          )
        }
      />
      <Route
        path={ROUTE_PATHS.home}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <HomeScreen
              // UI 仕様 (home.mdx): テーマ入力画面は挟まず直接対話を開始する
              // (タイトルは最初の発話から自動生成 — 2026-08-07 user 決定で newConsultation 画面を廃止)
              onStartConsultation={() => void handleStartConsultation()}
              onProblemList={() => void handleOpenProblemList()}
              onSpecPreview={isDev ? () => transition("specPreview") : undefined}
            />
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.specPreview}
        element={
          <ProtectedRoute authStatus={authStatus}>
            {isDev && DevSpecMdxPreview ? (
              <Stack spacing={2}>
                <Button
                  variant="text"
                  onClick={() => transition("home")}
                  sx={{ width: "fit-content" }}
                >
                  ホームへ
                </Button>
                <React.Suspense fallback={<Typography>Loading UI specs...</Typography>}>
                  <DevSpecMdxPreview />
                </React.Suspense>
              </Stack>
            ) : (
              <Navigate to={ROUTE_PATHS.home} replace />
            )}
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.session}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <RouteStateGuard when={session !== null} redirectTo={ROUTE_PATHS.home}>
              <SessionScreen
                session={session!}
                draftMessage={draftMessage}
                loading={loading}
                speaking={speaking}
                ttsStatus={ttsStatus}
                ttsEnabled={ttsEnabled}
                voiceError={voiceError}
                previewEnabled={previewEnabled}
                preview={preview}
                previewStatus={previewStatus}
                onRefreshPreview={handleRefreshPreview}
                pendingApproval={pendingApproval}
                onRespondToApproval={handleRespondToApproval}
                offeredChoices={offeredChoices}
                onSelectChoice={handleSelectChoice}
                onDraftMessageChange={setDraftMessage}
                onSendMessage={handleSendMessage}
                onToggleTtsEnabled={toggleTtsEnabled}
                onStopSpeaking={stopSpeaking}
                onCrisisSupport={() => transition("crisisSupport")}
                onPause={() => transition("paused")}
                onExtract={handleExtract}
              />
            </RouteStateGuard>
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.paused}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <RouteStateGuard when={session !== null} redirectTo={ROUTE_PATHS.home}>
              <PausedScreen onBackHome={() => transition("home")} />
            </RouteStateGuard>
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.crisisSupport}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <RouteStateGuard when={session !== null} redirectTo={ROUTE_PATHS.home}>
              <CrisisSupportScreen onBackSession={() => transition("session")} />
            </RouteStateGuard>
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.settings}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <SettingsScreen
              themeMode={themeMode}
              onToggleTheme={onToggleTheme}
              speedScale={speedScale}
              onChangeSpeedScale={onChangeSpeedScale}
            />
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.extractReview}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <RouteStateGuard when={extraction !== null} redirectTo={ROUTE_PATHS.home}>
              <ExtractReviewScreen
                extraction={extraction!}
                loading={loading}
                onDismiss={(problemId) => void handleDismissExtracted(problemId)}
                onGoToList={() => void handleOpenProblemList()}
              />
            </RouteStateGuard>
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.problemList}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <ProblemListScreen
              problems={problems}
              loading={loading}
              onOpen={(id) => void handleOpenProblem(id)}
              onBackHome={() => transition("home")}
            />
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.problemDetail}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <RouteStateGuard when={selectedProblem !== null} redirectTo={ROUTE_PATHS.problemList}>
              <ProblemDetailScreen
                problem={selectedProblem!}
                loading={loading}
                onBack={() => void handleOpenProblemList()}
                onTriage={(input) => void handleTriage(input)}
                onCreatePlan={(id) => void handleCreateProblemPlan(id)}
              />
            </RouteStateGuard>
          </ProtectedRoute>
        }
      />
      <Route
        path="*"
        element={
          <Navigate to={isAuthenticated ? ROUTE_PATHS.home : ROUTE_PATHS.onboarding} replace />
        }
      />
    </Routes>
  );
}
