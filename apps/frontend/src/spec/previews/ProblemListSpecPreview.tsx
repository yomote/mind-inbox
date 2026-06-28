import { ProblemListScreen } from "../../components/screens/ProblemListScreen";
import { sampleProblems } from "./problemFixtures";

export function ProblemListSpecPreview() {
  return (
    <ProblemListScreen
      problems={sampleProblems}
      loading={false}
      onOpen={() => {}}
      onBackHome={() => {}}
    />
  );
}
