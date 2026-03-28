import { ActionPlanScreen } from "../../components/screens/ActionPlanScreen";

export function ActionPlanSpecPreview() {
  return (
    <ActionPlanScreen
      plan={{
        title: "48時間アクションプラン",
        steps: [
          "最優先: 今日やることを3つに絞る",
          "明日の午前中に15分だけ着手する",
          "完了後に気分を10段階で記録する",
        ],
      }}
      onSave={() => {}}
    />
  );
}
