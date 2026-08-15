// ルート定義。Router.tsx (コンポーネント) から分離している —
// eslint-plugin-react-refresh 0.5 の only-export-components が
// 「コンポーネントと定数の同居ファイルは Fast Refresh が効かない」を error にするため。
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
