export {
  startNewConsultation,
  sendMessage,
  organizeResult,
  createActionPlan,
} from "./consultation";

export { loadHistories, saveHistory } from "./history";

export type {
  ActionPlan,
  ChatMessage,
  ChatRole,
  ConsultationSession,
  HistoryItem,
  OrganizedResult,
  Screen,
} from "../mockApi";
