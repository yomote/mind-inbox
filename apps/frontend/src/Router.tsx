import * as React from "react";
import { Button, Stack, Typography } from "@mui/material";
import { Navigate, Route, Routes } from "react-router-dom";
import type { PaletteMode } from "@mui/material";
import type {
  ActionPlan,
  ConsultationSession,
  ExtractionResult,
  HistoryItem,
  OrganizedResult,
  Problem,
  TriageInput,
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
import { ExtractReviewScreen } from "./components/screens/ExtractReviewScreen";
import { ProblemListScreen } from "./components/screens/ProblemListScreen";
import { ProblemDetailScreen } from "./components/screens/ProblemDetailScreen";

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
  extraction: ExtractionResult | null;
  problems: Problem[];
  selectedProblem: Problem | null;
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
  handleExtract: () => Promise<void>;
  handleCreatePlan: () => Promise<void>;
  handleSaveAndGoHistory: () => void;
  openHistoryResult: (item: HistoryItem) => void;
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
  extraction,
  problems,
  selectedProblem,
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
  handleExtract,
  handleCreatePlan,
  handleSaveAndGoHistory,
  openHistoryResult,
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
              onNewConsultation={() => void handleStartConsultation()}
              onProblemList={() => void handleOpenProblemList()}
              onHistory={() => transition("history")}
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
        path={ROUTE_PATHS.newConsultation}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <NewConsultationScreen
              concern={concern}
              loading={loading}
              voiceError={voiceError}
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
            <RouteStateGuard when={session !== null} redirectTo={ROUTE_PATHS.home}>
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
        path={ROUTE_PATHS.result}
        element={
          <ProtectedRoute authStatus={authStatus}>
            <RouteStateGuard when={result !== null} redirectTo={ROUTE_PATHS.home}>
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
            <SettingsScreen themeMode={themeMode} onToggleTheme={onToggleTheme} />
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
