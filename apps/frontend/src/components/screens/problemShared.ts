import type { Affect, ProblemStatus, Theme } from "../../mockApi";

/**
 * Problem / Mention 系画面の共有表示ヘルパ。
 * 型（Theme / ProblemStatus）は BFF の domain.ts が真実。ここは表示用のラベル / 色だけ持つ。
 * THEME_OPTIONS は Theme[] で縛るので、domain 側の union が変われば型エラーで気づける。
 */

export const THEME_OPTIONS: Theme[] = [
  "仕事・キャリア",
  "お金",
  "心と体",
  "家族・パートナー",
  "人間関係",
  "自己理解・生き方",
  "日常・生活",
  "未分類",
];

export const STATUS_LABEL: Record<ProblemStatus, string> = {
  open: "追跡中",
  resolved: "解決",
  shelved: "棚卸し",
};

export const STATUS_COLOR: Record<ProblemStatus, "default" | "success" | "warning"> = {
  open: "default",
  resolved: "success",
  shelved: "warning",
};

export const AFFECT_COLOR: Record<Affect["valence"], "error" | "default" | "success"> = {
  negative: "error",
  neutral: "default",
  positive: "success",
};

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ja-JP", {
    month: "numeric",
    day: "numeric",
  });
}

/** 「N日前 / 今日」相当の相対表示（最終言及の体感に使う）。 */
export function relativeDays(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (days <= 0) return "今日";
  if (days === 1) return "昨日";
  if (days < 7) return `${days}日前`;
  if (days < 30) return `${Math.floor(days / 7)}週間前`;
  return `${Math.floor(days / 30)}ヶ月前`;
}
