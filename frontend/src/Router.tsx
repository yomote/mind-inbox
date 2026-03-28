import * as React from "react";
import { Button, Stack, Typography } from "@mui/material";
import { Navigate, Route, Routes } from "react-router-dom";
import type { PaletteMode } from "@mui/material";
import type {
  ActionPlan,
  ConsultationSession,
  HistoryItem,
  OrganizedResult,
} from "./mockApi";
import { SessionScreen } from "./components/session/SessionScreen";
import { OnboardingScreen } from "./components/screens/OnboardingScreen";
import { HomeScreen } from "./components/screens/HomeScreen";
import { NewConsultationScreen } from "./components/screens/NewConsultationScreen";
import { PausedScreen } from "./components/screens/PausedScreen";
import { CrisisSupportScreen } from "./components/screens/CrisisSupportScreen";
import { ResultScreen } from "./components/screens/ResultScreen";
import { ActionPlanScreen } from "./components/screens/ActionPlanScreen";
import { HistoryScreen } from "./components/screens/HistoryScreen";
import { SettingsScreen } from "./components/screens/SettingsScreen";

export type AuthStatus = "loading" | "authenticated" | "anonymous";

export const ROUTE_PATHS = {
  onboarding: "/",
  home: "/home",
  specPreview: "/spec",
  newConsultation: "/consultations/new",
  session: "/consultations/current",
  result: "/consultations/current/result",
  actionPlan: "/consultations/current/action-plan",
  history: "/history",
  settings: "/settings",
  paused: "/consultations/current/paused",
  crisisSupport: "/consultations/current/crisis-support",
} as const;

export type AppRoute = keyof typeof ROUTE_PATHS;

type AppRouterProps = {
  authStatus: AuthStatus;
  isAuthenticated: boolean;
  isDev: boolean;
  DevSpecMdxPreview: React.LazyExoticComponent<
    React.ComponentType<unknown>
  > | null;
  concern: string;
  loading: boolean;
  session: ConsultationSession | null;
  draftMessage: string;
  sttSupported: boolean;
  listening: boolean;
  interimTranscript: string;
  speaking: boolean;
  ttsEnabled: boolean;
  voiceError: string | null;
  result: OrganizedResult | null;
  plan: ActionPlan | null;
  histories: HistoryItem[];
  selectedHistory: HistoryItem | null;
  themeMode: PaletteMode;
  onToggleTheme: () => void;
  transition: (next: AppRoute) => void;
  setConcern: (value: string) => void;
  setDraftMessage: (value: string) => void;
  handleLogin: () => void;
  handleStartConsultation: () => Promise<void>;
  handleSendMessage: () => Promise<void>;
  toggleListening: () => void;
  toggleTtsEnabled: () => void;
  stopSpeaking: () => void;
  handleOrganize: () => Promise<void>;
  handleCreatePlan: () => Promise<void>;
  handleSaveAndGoHistory: () => void;
  openHistoryResult: (item: HistoryItem) => void;
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
  concern,
  loading,
  session,
  draftMessage,
  sttSupported,
  listening,
  interimTranscript,
  speaking,
  ttsEnabled,
  voiceError,
  result,
  plan,
  histories,
  selectedHistory,
  themeMode,
  onToggleTheme,
  transition,
  setConcern,
  setDraftMessage,
  handleLogin,
  handleStartConsultation,
  handleSendMessage,
  toggleListening,
  toggleTtsEnabled,
  stopSpeaking,
  handleOrganize,
  handleCreatePlan,
  handleSaveAndGoHistory,
  openHistoryResult,
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
              onNewConsultation={() => transition("newConsultation")}
              onHistory={() => transition("history")}
              onSpecPreview={
                isDev ? () => transition("specPreview") : undefined
              }
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
                <React.Suspense
                  fallback={<Typography>Loading UI specs...</Typography>}
                >
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
        path={ROUTE_PATHS.newConsultation}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <NewConsultationScreen
              concern={concern}
              loading={loading}
              onConcernChange={setConcern}
              onBack={() => transition("home")}
              onStart={handleStartConsultation}
            />
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.session}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <RouteStateGuard
              when={session !== null}
              redirectTo={ROUTE_PATHS.home}
            >
              <SessionScreen
                session={session!}
                draftMessage={draftMessage}
                loading={loading}
                sttSupported={sttSupported}
                listening={listening}
                interimTranscript={interimTranscript}
                speaking={speaking}
                ttsEnabled={ttsEnabled}
                voiceError={voiceError}
                onDraftMessageChange={setDraftMessage}
                onSendMessage={handleSendMessage}
                onToggleListening={toggleListening}
                onToggleTtsEnabled={toggleTtsEnabled}
                onStopSpeaking={stopSpeaking}
                onCrisisSupport={() => transition("crisisSupport")}
                onPause={() => transition("paused")}
                onOrganize={handleOrganize}
              />
            </RouteStateGuard>
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.paused}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <RouteStateGuard
              when={session !== null}
              redirectTo={ROUTE_PATHS.home}
            >
              <PausedScreen onBackHome={() => transition("home")} />
            </RouteStateGuard>
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.crisisSupport}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <RouteStateGuard
              when={session !== null}
              redirectTo={ROUTE_PATHS.home}
            >
              <CrisisSupportScreen
                onBackSession={() => transition("session")}
              />
            </RouteStateGuard>
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.result}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <RouteStateGuard
              when={result !== null}
              redirectTo={ROUTE_PATHS.home}
            >
              <ResultScreen
                result={result!}
                loading={loading}
                onHistory={() => transition("history")}
                onCreatePlan={handleCreatePlan}
              />
            </RouteStateGuard>
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.actionPlan}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <RouteStateGuard
              when={plan !== null}
              redirectTo={result ? ROUTE_PATHS.result : ROUTE_PATHS.home}
            >
              <ActionPlanScreen plan={plan!} onSave={handleSaveAndGoHistory} />
            </RouteStateGuard>
          </ProtectedRoute>
        }
      />
      <Route
        path={ROUTE_PATHS.history}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <HistoryScreen
              histories={histories}
              selectedHistory={selectedHistory}
              onBackHome={() => transition("home")}
              onOpenResult={openHistoryResult}
            />
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
            />
          </ProtectedRoute>
        }
      />
      <Route
        path="*"
        element={
          <Navigate
            to={isAuthenticated ? ROUTE_PATHS.home : ROUTE_PATHS.onboarding}
            replace
          />
        }
      />
    </Routes>
  );
}
