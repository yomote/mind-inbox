import * as React from "react";
import { NewConsultationScreen } from "../../components/screens/NewConsultationScreen";

export function NewConsultationSpecPreview() {
  const [concern, setConcern] = React.useState("仕事の優先順位が整理できない");

  return (
    <NewConsultationScreen
      concern={concern}
      loading={false}
      onConcernChange={setConcern}
      onBack={() => {}}
      onStart={() => {}}
    />
  );
}
