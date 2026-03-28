import { HistoryScreen } from "../../components/screens/HistoryScreen";

const histories = [
  {
    id: "h-1",
    title: "仕事の優先順位を整理",
    createdAt: new Date().toISOString(),
    result: {
      summary:
        "タスク過多による焦りが中心。優先順位の明確化で負荷を下げられる状態。",
      emotions: ["焦り", "疲労"],
      priorities: ["重要タスクの絞り込み", "期限交渉", "休憩確保"],
    },
    plan: {
      title: "1日リセットプラン",
      steps: ["朝5分で優先3件を決める", "昼に進捗確認", "夕方に振り返り"],
    },
  },
];

export function HistorySpecPreview() {
  return (
    <HistoryScreen
      histories={histories}
      selectedHistory={histories[0]}
      onBackHome={() => {}}
      onOpenResult={() => {}}
    />
  );
}
