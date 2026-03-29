import { ResultScreen } from "../../components/screens/ResultScreen";

export function ResultSpecPreview() {
  return (
    <ResultScreen
      result={{
        summary:
          "タスク過多による焦りが中心。優先順位の明確化で負荷を下げられる状態。",
        emotions: ["焦り", "疲労", "少しの希望"],
        priorities: ["重要タスクの絞り込み", "期限交渉", "休憩確保"],
      }}
      loading={false}
      onHistory={() => {}}
      onCreatePlan={() => {}}
    />
  );
}
