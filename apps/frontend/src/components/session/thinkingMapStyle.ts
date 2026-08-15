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

/**
 * status の色 (MUI の sx トークン)。**枠線と記号にだけ使う — ラベル本文には使わない**
 * (Codex P2 / WCAG 1.4.3)。
 *
 * ライトテーマの `warning.main` は白背景に対して約 3.1:1、`text.secondary` は約 5.5:1 で、
 * **本文の 4.5:1 を満たさない色が混じる**。ラベルを status 色で塗ると
 * 「仮説・未探索の話題名が弱視の人に読めない」状態になり、しかも画面上は色が付いて
 * 見えているだけなので気づけない。**ラベルは常に `text.primary`**、status は
 * **枠線・線種 (実線 / 破線 / 点線) + チップの文字 + 凡例**が持つ。
 *
 * `unexplored` に `text.disabled` (約 2.7:1) ではなく `text.secondary` を使うのは、
 * 枠線や記号も**非テキストの UI 部品として 3:1** が要るため (WCAG 1.4.11)。
 * 「濃さの順 (確定 > 仮説 > 未探索)」は保ったまま、下限だけ持ち上げてある。
 */
export const STATUS_COLOR: Record<ThinkingNodeStatus, string> = {
  confirmed: "text.primary",
  tentative: "warning.main",
  unexplored: "text.secondary",
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
  /**
   * **枠線と枝の色**だけに使う実値 (SVG 用)。**ラベル本文には使わない** —
   * 本文の色は `labelColor()` (= `text.primary`) に固定してある (Codex P2 / WCAG 1.4.3)。
   */
  strokeColor: string;
  /** 線の描き方。確かさが下がるほど線が途切れる。 */
  dash: string | undefined;
  strokeWidth: number;
};

/**
 * 図 (SVG) 側の status → 枠線の見た目。**箇条書き側の `STATUS_COLOR` と同じ割り当て**を
 * theme の実値に解決したもの (`text.primary` / `warning.main` / `text.secondary`)。
 */
export function statusStyle(theme: Theme, status: ThinkingNodeStatus): ThinkingStatusStyle {
  switch (status) {
    case "confirmed":
      return { strokeColor: theme.palette.text.primary, dash: undefined, strokeWidth: 1.8 };
    case "tentative":
      return { strokeColor: theme.palette.warning.main, dash: "5 4", strokeWidth: 1.5 };
    case "unexplored":
      return { strokeColor: theme.palette.text.secondary, dash: "2 4", strokeWidth: 1.2 };
  }
}

/**
 * ラベル本文の色。**status では変えない** — 変えた瞬間に、色の薄い status の話題名が
 * 本文コントラスト (4.5:1) を割る。status は枠線・線種・チップの文字・凡例が持つので、
 * ここで色を変えても情報は増えず、読めなくなるだけ (Codex P2)。
 */
export function labelColor(theme: Theme): string {
  return theme.palette.text.primary;
}
