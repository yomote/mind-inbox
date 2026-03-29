import * as React from "react";
import { Button, Link, Paper, Stack } from "@mui/material";
import ReadmeDoc from "../../../docs/frontend/ui_specs/README.mdx";
import OnboardingDoc from "../../../docs/frontend/ui_specs/onboarding.mdx";
import HomeDoc from "../../../docs/frontend/ui_specs/home.mdx";
import NewConsultationDoc from "../../../docs/frontend/ui_specs/new-consultation.mdx";
import DialogueSessionDoc from "../../../docs/frontend/ui_specs/dialogue session.mdx";
import ResultDoc from "../../../docs/frontend/ui_specs/result.mdx";
import ActionPlanDoc from "../../../docs/frontend/ui_specs/action-plan.mdx";
import HistoryDoc from "../../../docs/frontend/ui_specs/history.mdx";
import SettingsDoc from "../../../docs/frontend/ui_specs/settings.mdx";
import PausedDoc from "../../../docs/frontend/ui_specs/paused.mdx";
import CrisisSupportDoc from "../../../docs/frontend/ui_specs/crisis-support.mdx";

type DocKey =
  | "readme"
  | "onboarding"
  | "home"
  | "newConsultation"
  | "dialogueSession"
  | "result"
  | "actionPlan"
  | "history"
  | "settings"
  | "paused"
  | "crisisSupport";

const docs: Record<DocKey, React.ComponentType<Record<string, unknown>>> = {
  readme: ReadmeDoc,
  onboarding: OnboardingDoc,
  home: HomeDoc,
  newConsultation: NewConsultationDoc,
  dialogueSession: DialogueSessionDoc,
  result: ResultDoc,
  actionPlan: ActionPlanDoc,
  history: HistoryDoc,
  settings: SettingsDoc,
  paused: PausedDoc,
  crisisSupport: CrisisSupportDoc,
};

const labels: Array<{ key: DocKey; label: string }> = [
  { key: "readme", label: "一覧" },
  { key: "onboarding", label: "オンボード" },
  { key: "home", label: "ホーム" },
  { key: "newConsultation", label: "新規相談" },
  { key: "dialogueSession", label: "対話" },
  { key: "result", label: "整理結果" },
  { key: "actionPlan", label: "行動プラン" },
  { key: "history", label: "履歴" },
  { key: "settings", label: "設定" },
  { key: "paused", label: "中断" },
  { key: "crisisSupport", label: "危機" },
];

const hrefToDocKey: Record<string, DocKey> = {
  "./README.mdx": "readme",
  "./onboarding.mdx": "onboarding",
  "./home.mdx": "home",
  "./new-consultation.mdx": "newConsultation",
  "./dialogue%20session.mdx": "dialogueSession",
  "./dialogue session.mdx": "dialogueSession",
  "./result.mdx": "result",
  "./action-plan.mdx": "actionPlan",
  "./history.mdx": "history",
  "./settings.mdx": "settings",
  "./paused.mdx": "paused",
  "./crisis-support.mdx": "crisisSupport",
};

export function DevSpecMdxPreview() {
  const [active, setActive] = React.useState<DocKey>("readme");
  const ActiveDoc = docs[active];

  const mdxComponents = React.useMemo(
    () => ({
      a: ({ href, children, ...props }: React.ComponentProps<"a">) => {
        const key = href ? hrefToDocKey[href] : undefined;
        if (key) {
          return (
            <Link
              href={href}
              onClick={(e) => {
                e.preventDefault();
                setActive(key);
              }}
              {...props}
            >
              {children}
            </Link>
          );
        }

        return (
          <Link href={href} {...props}>
            {children}
          </Link>
        );
      },
    }),
    [],
  );

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        {labels.map((item) => (
          <Button
            key={item.key}
            size="small"
            variant={item.key === active ? "contained" : "outlined"}
            onClick={() => setActive(item.key)}
          >
            {item.label}
          </Button>
        ))}
      </Stack>
      <Paper sx={{ p: 3, borderRadius: 3 }}>
        <ActiveDoc components={mdxComponents} />
      </Paper>
    </Stack>
  );
}
