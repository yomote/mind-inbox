import { ExtractReviewScreen } from "../../components/screens/ExtractReviewScreen";
import type { ExtractionResult } from "../../api";

const extraction: ExtractionResult = {
  sessionId: "s-preview",
  newProblemCount: 1,
  updatedProblemCount: 1,
  items: [
    {
      mention: {
        id: "m-new-1",
        sessionId: "s-preview",
        dumpId: "s-preview",
        createdAt: new Date().toISOString(),
        statement: "来週のプレゼンが不安で気が重い。",
        excerpt: "人前で話すの本当に苦手で今から憂鬱",
        affect: { label: "緊張", valence: "negative", intensity: 0.65 },
        proposedTheme: "仕事・キャリア",
        proposedTags: ["プレゼン", "緊張"],
        problemId: "p-new-1",
        groupingConfidence: null,
      },
      grouping: {
        kind: "new",
        problemId: "p-new-1",
        problemTitle: "来週のプレゼンが不安",
        problemTheme: "仕事・キャリア",
        isRecurrence: false,
        mentionCount: 1,
        reignited: false,
        groupingConfidence: null,
      },
    },
    {
      mention: {
        id: "m-recur-1",
        sessionId: "s-preview",
        dumpId: "s-preview",
        createdAt: new Date().toISOString(),
        statement: "やっぱり転職のことが頭から離れない。",
        excerpt: "面談だけでも受けてみようかな…でも怖い",
        affect: { label: "迷い", valence: "negative", intensity: 0.58 },
        proposedTheme: "仕事・キャリア",
        proposedTags: ["転職", "迷い"],
        problemId: "p-career",
        groupingConfidence: 0.88,
      },
      grouping: {
        kind: "existing",
        problemId: "p-career",
        problemTitle: "転職すべきか迷っている",
        problemTheme: "仕事・キャリア",
        isRecurrence: true,
        mentionCount: 4,
        reignited: false,
        groupingConfidence: 0.88,
      },
    },
  ],
};

export function ExtractReviewSpecPreview() {
  return (
    <ExtractReviewScreen
      extraction={extraction}
      loading={false}
      onDismiss={() => {}}
      onGoToList={() => {}}
    />
  );
}
