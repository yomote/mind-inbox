import { ProblemDetailScreen } from "../../components/screens/ProblemDetailScreen";
import { sampleProblems } from "./problemFixtures";

export function ProblemDetailSpecPreview() {
  return (
    <ProblemDetailScreen
      problem={sampleProblems[0]}
      loading={false}
      onBack={() => {}}
      onTriage={() => {}}
      onCreatePlan={() => {}}
    />
  );
}
