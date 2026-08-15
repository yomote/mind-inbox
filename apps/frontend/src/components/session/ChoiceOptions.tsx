import { Chip, Paper, Stack, Typography } from "@mui/material";

/**
 * AI が提示した選択肢 (#432-b / dialogue-session.mdx §5.10)。
 *
 * **承認カード (§5.9) とは別物**。似ているのは見た目だけで、意味は逆に近い:
 *
 * | | 承認カード | 選択肢 |
 * | --- | --- | --- |
 * | サーバの状態 | 承認待ちで**止まっている** | 止まっていない (ターンは完了済み) |
 * | 押さなかったら | checkpoint が宙に浮く → 却下を送る必要がある | **何も起きない** (正常) |
 * | 入力欄 | 承認を先に片付けさせる | **塞がない** (自由記述との混合が裁定) |
 *
 * したがってここでは (1) 入力欄を disabled にしない (2) 押さなかったことをサーバへ
 * 知らせない (3) 「選ばないと進めない」と読める文言を出さない。押した文言は
 * **そのまま次の発話として**送られる。
 */
type ChoiceOptionsProps = {
  /** 空配列なら何も描画しない (空のチップ帯を全ターンに出さない)。 */
  choices: string[];
  /** 送信中は二度押しさせない (連打で同じ発話が 2 通送られる)。 */
  loading?: boolean;
  onSelect: (choice: string) => void;
};

export function ChoiceOptions({ choices, loading, onSelect }: ChoiceOptionsProps) {
  if (choices.length === 0) return null;

  return (
    <Paper data-testid="choice-options" variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
      <Stack spacing={1}>
        <Typography variant="caption" color="text.secondary">
          選んでも、自由に書いてもかまいません。
        </Typography>
        <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
          {choices.map((choice) => (
            <Chip
              key={choice}
              label={choice}
              // Chip は既定で button ロールにならない。onClick を付けると clickable に
              // なるが、**名前で引ける要素として検証したい**ので明示する (§5.10 機械検証)
              component="button"
              type="button"
              variant="outlined"
              color="primary"
              disabled={loading}
              onClick={() => onSelect(choice)}
            />
          ))}
        </Stack>
      </Stack>
    </Paper>
  );
}
