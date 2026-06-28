export {
  startNewConsultation,
  sendMessage,
  organizeResult,
  createActionPlan,
} from "./consultation";

export { loadHistories, saveHistory } from "./history";

export {
  extractMentions,
  loadProblems,
  loadProblem,
  triageProblem,
  createProblemPlan,
} from "./problems";

export type {
  ActionPlan,
  Affect,
  ChatMessage,
  ChatRole,
  ConsultationSession,
  ExtractedItem,
  ExtractionResult,
  GroupingOutcome,
  HistoryItem,
  Mention,
  OrganizedResult,
  Problem,
  ProblemFilter,
  ProblemStatus,
  Screen,
  Theme,
  TriageAction,
  TriageInput,
} from "../mockApi";
