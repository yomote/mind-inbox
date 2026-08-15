/**
 * 「AI の整理」の色・記号・語彙の**唯一の割り当て表** (#433 / §5.8.1)。
 *
 * 箇条書き (`ThinkingMapTree`) と図 (`ThinkingMapGraph`) が**同じ表**を見るためにここへ出した。
 * 2 箇所で別々に持つと、片方だけ status の対応がずれても画面は普通に描画され、
 * 「確定なのに未探索の色」という食い違いに誰も気づけない。
 */

import type { Theme } from "@mui/material/styles";
import type { ThinkingNodeKind, ThinkingNodeStatus } from "../../api";

/** status = どれだけ確かか。色は「濃さ」であって数値ではない (定量の顔をさせない)。 */
export const STATUS_LABEL: Record<ThinkingNodeStatus, string> = {
  confirmed: "確定",
  tentative: "仮説",
  unexplored: "未探索",
};

/** 箇条書き側の色 (MUI の sx トークン)。 */
export const STATUS_COLOR: Record<ThinkingNodeStatus, string> = {
  confirmed: "text.primary",
  tentative: "warning.main",
  unexplored: "text.disabled",
};

export const STATUS_MARKER: Record<ThinkingNodeStatus, string> = {
  confirmed: "●",
  tentative: "◐",
  unexplored: "○",
};

/**
 * kind = それが何か。topic は既定なのでチップを出さない (行が読みづらくなる)。
 * status の語 (確定 / 仮説 / 未探索) と重ならない言葉を選ぶ — 「未探索」の隣に
 * 「聞けていない」を並べても情報が増えず、2 軸あることが伝わらない。
 */
export const KIND_LABEL: Partial<Record<ThinkingNodeKind, string>> = {
  hypothesis: "AI の見立て",
  unknown: "確かめたいこと",
};

/** 図の凡例に出す語 (topic も含めて全部書く — 形の意味は文字でも読めるようにする)。 */
export const KIND_SHAPE_LABEL: Record<ThinkingNodeKind, string> = {
  topic: "話題 = 角丸",
  hypothesis: "AI の見立て = 錠剤形",
  unknown: "確かめたいこと = 六角形",
};

export type ThinkingStatusStyle = {
  /** 枠線と文字の色 (SVG 用に解決済みの実値)。 */
  color: string;
  /** 線の描き方。確かさが下がるほど線が途切れる。 */
  dash: string | undefined;
  strokeWidth: number;
};

/**
 * 図 (SVG) 側の status → 見た目。**箇条書き側の `STATUS_COLOR` と同じ意味**を
 * theme の実値に解決したもの (`text.primary` / `warning.main` / `text.disabled`)。
 */
export function statusStyle(theme: Theme, status: ThinkingNodeStatus): ThinkingStatusStyle {
  switch (status) {
    case "confirmed":
      return { color: theme.palette.text.primary, dash: undefined, strokeWidth: 1.8 };
    case "tentative":
      return { color: theme.palette.warning.main, dash: "5 4", strokeWidth: 1.5 };
    case "unexplored":
      return { color: theme.palette.text.disabled, dash: "2 4", strokeWidth: 1.2 };
  }
}
