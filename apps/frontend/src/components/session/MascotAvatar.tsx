import { Box } from "@mui/material";
import type { MascotState } from "./mascotState";

// 状態 3 種の定義と既存進行状態からの写像は mascotState.ts (§5.6)。
export type { MascotState } from "./mascotState";

const STATE_LABEL: Record<MascotState, string> = {
  idle: "ガイドのマスコット (待機中)",
  preparing: "ガイドのマスコット (準備中)",
  speaking: "ガイドのマスコット (発話中)",
};

type MascotAvatarProps = {
  state: MascotState;
  /** 表示サイズ (px)。既定 44。 */
  size?: number;
};

/**
 * Mind Inbox の独自マスコット (ADR 0039 D5)。
 *
 * **差し替え可能な Avatar 部品**として切り出してある — キャラクターを変えるときは
 * このファイルの SVG を差し替えるだけでよく、状態機械 (`MascotState`) と観測点
 * (`data-mascot-state`) は変えない。絵は手描きのインライン SVG (ライセンスフリー)。
 *
 * 状態は静的な表情 (目・口) でも区別できるようにし、アニメーションは追加の表現に
 * とどめる — `prefers-reduced-motion: reduce` ではアニメーションだけを止める。
 */
export function MascotAvatar({ state, size = 44 }: MascotAvatarProps) {
  return (
    <Box
      component="span"
      data-mascot-state={state}
      role="img"
      aria-label={STATE_LABEL[state]}
      sx={{
        display: "inline-flex",
        flexShrink: 0,
        width: size,
        height: size,
        "@keyframes mi-mascot-bob": {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-2px)" },
        },
        "@keyframes mi-mascot-pulse": {
          "0%, 100%": { transform: "scale(1)" },
          "50%": { transform: "scale(1.05)" },
        },
        "@keyframes mi-mascot-dot": {
          "0%, 60%, 100%": { opacity: 0.25 },
          "30%": { opacity: 1 },
        },
        "@keyframes mi-mascot-talk": {
          "0%, 100%": { transform: "scaleY(0.45)" },
          "50%": { transform: "scaleY(1)" },
        },
        "@keyframes mi-mascot-wave": {
          "0%, 100%": { opacity: 0.15 },
          "50%": { opacity: 0.9 },
        },
        "& .mi-mascot-body": {
          transformOrigin: "50% 60%",
          animation:
            state === "idle"
              ? "mi-mascot-bob 3s ease-in-out infinite"
              : state === "preparing"
                ? "mi-mascot-pulse 1.2s ease-in-out infinite"
                : "mi-mascot-bob 1.6s ease-in-out infinite",
        },
        "& .mi-mascot-dot": { animation: "mi-mascot-dot 1.2s ease-in-out infinite" },
        "& .mi-mascot-dot-2": { animationDelay: "0.2s" },
        "& .mi-mascot-dot-3": { animationDelay: "0.4s" },
        "& .mi-mascot-mouth-open": {
          transformOrigin: "32px 40px",
          animation: "mi-mascot-talk 0.4s ease-in-out infinite",
        },
        "& .mi-mascot-wave": { animation: "mi-mascot-wave 0.9s ease-in-out infinite" },
        "& .mi-mascot-wave-2": { animationDelay: "0.3s" },
        // 動きの低減を選んだ環境ではアニメーションを止める (状態は静的な表情で伝わる)。
        "@media (prefers-reduced-motion: reduce)": {
          "& *": { animation: "none !important" },
        },
      }}
    >
      <svg viewBox="0 0 64 64" width="100%" height="100%" aria-hidden="true">
        <g className="mi-mascot-body">
          {/* 芽 (Mind Inbox = 頭の中を育てる) */}
          <path
            d="M32 14 C32 10 34 7 38 6 C38 10 36 13 32 14 Z"
            fill="#6FBF8E"
            stroke="#38735B"
            strokeWidth="1.5"
            strokeLinejoin="round"
          />
          <path d="M32 14 L32 18" stroke="#38735B" strokeWidth="1.8" strokeLinecap="round" />
          {/* 体 (まる) */}
          <ellipse
            cx="32"
            cy="38"
            rx="20"
            ry="19"
            fill="#A5DEC8"
            stroke="#38735B"
            strokeWidth="2"
          />
          {/* ほっぺ */}
          <circle cx="20" cy="42" r="3" fill="#F2A6A6" opacity="0.7" />
          <circle cx="44" cy="42" r="3" fill="#F2A6A6" opacity="0.7" />

          {/* 目 — 状態で変える */}
          {state === "preparing" ? (
            // 準備中: 閉じ目 (考えている / 息を吸っている)
            <>
              <path
                d="M22 34 Q25 36 28 34"
                stroke="#38735B"
                strokeWidth="2"
                strokeLinecap="round"
                fill="none"
              />
              <path
                d="M36 34 Q39 36 42 34"
                stroke="#38735B"
                strokeWidth="2"
                strokeLinecap="round"
                fill="none"
              />
            </>
          ) : (
            // 待機 / 発話中: ひらき目
            <>
              <circle cx="25" cy="34" r="2.4" fill="#38735B" />
              <circle cx="39" cy="34" r="2.4" fill="#38735B" />
            </>
          )}

          {/* 口 — 状態で変える */}
          {state === "idle" && (
            <path
              d="M27 41 Q32 45 37 41"
              stroke="#38735B"
              strokeWidth="2"
              strokeLinecap="round"
              fill="none"
            />
          )}
          {state === "preparing" && <circle cx="32" cy="42" r="2.2" fill="#38735B" />}
          {state === "speaking" && (
            <ellipse
              className="mi-mascot-mouth-open"
              cx="32"
              cy="42"
              rx="4.5"
              ry="4"
              fill="#38735B"
            />
          )}
        </g>

        {/* 準備中: 考え中ドット */}
        {state === "preparing" && (
          <g>
            <circle className="mi-mascot-dot" cx="46" cy="14" r="2" fill="#38735B" />
            <circle
              className="mi-mascot-dot mi-mascot-dot-2"
              cx="52"
              cy="11"
              r="2.5"
              fill="#38735B"
            />
            <circle className="mi-mascot-dot mi-mascot-dot-3" cx="58" cy="7" r="3" fill="#38735B" />
          </g>
        )}

        {/* 発話中: 音波 */}
        {state === "speaking" && (
          <g fill="none" stroke="#38735B" strokeWidth="2" strokeLinecap="round">
            <path className="mi-mascot-wave" d="M55 32 Q58 38 55 44" />
            <path className="mi-mascot-wave mi-mascot-wave-2" d="M59 29 Q63 38 59 47" />
          </g>
        )}
      </svg>
    </Box>
  );
}
