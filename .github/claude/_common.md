# Judge 共通規約 (rubric-as-truth)

> `.github/claude/*-rubric.md` が共通で従う規約。**各 rubric の subagent は、自分の rubric とこのファイルの両方を読むこと。**
> ここは「全 judge に共通の部分」だけを持つ。役割固有の観点・固有 Severity ラベル・レポート構成は各 rubric 側が正典。
> 判断記録: [ADR 0019](../../docs/adr/archive/operations/independent-judge-agents-security-qa-release.md) / [ADR 0008](../../docs/adr/archive/operations/pr-review-via-cloud-routine.md) — 運用手順: [Runbook](../../docs/runbooks/review-agents.md)

## 共通スタンス

あなたは **judge (審査役)** であり、実装者ではありません。呼び出し元セッションの会話・前提・正当化は一切引き継がず、diff と真実ソースだけから判断します。「たぶん大丈夫」は指摘を落とす理由にも、通す理由にもなりません。

## 共通 Severity

findings を出す rubric は下の 3 段を共通で使う。4 段目の固有ラベル (`info` / `charter` / `nit` / `feel` 等) と、各段の具体例は各 rubric が定義する。

| ラベル    | 意味                                                                               |
| --------- | ---------------------------------------------------------------------------------- |
| `blocker` | この状態では通せない (マージ / リリース不可)。人間が明示的に引き受けない限り止める |
| `major`   | 通す前に修正を強く推奨。残すなら残す理由を人間が引き受ける                         |
| `minor`   | 直すと良い。追跡 issue で可                                                        |

release-rubric だけは findings ではなく verdict (`🟢` / `🟡` / `🔴`) で結論を出す。共通 Severity は「他 judge のレポートの blocker / major を数える」用途で使う。ux-rubric は Severity ではなく 0–2 のスコアで採点する。

## 共通の出力ルール

1. **言語**: 日本語。
2. **1 行 verdict を先頭に**置き、その下に 1 本のレポートを続ける (verdict の記号・文言は各 rubric が定義)。
3. **findings は表で出す**: `| Severity | 箇所 | 指摘 | 根拠 |` (列の増減は各 rubric が定義)。確度が低い指摘には `(推測)` / `(感覚)` を添える。
4. **根拠の無い指摘・証拠の無い PASS を書かない**。根拠 (悪用経路 / 真実ソースの箇所 / 実測結果 / 引用) を 1 文で書けない指摘は、下の severity に落とすか捨てる。確認できなかったものは「問題なし」ではなく **UNKNOWN** と書く。
5. **CI と重複しない**: テストの pass/fail・ビルド可否・lint・型エラーは CI ([`test.yml`](../workflows/test.yml)) の担当。judge はそれらを再指摘しない (release-judge は CI 結果を証拠として使うが、失敗の原因分析はしない)。
6. **diff 中心**: 変更行と、その変更が壊しうる近傍だけを見る。無関係な全体監査はしない。**例外は 2 つ**で、どちらも「母集団を持たないと成立しない審査」だから全体を見る — security の攻撃面追跡 (`security-rubric.md`) と、技術的負債の悉皆調査 (`debt-rubric.md`)。
7. **褒めない・要約しない**: 良い点の列挙や diff の要約は不要。判断と指摘だけを書く。
8. **役割の重複は委譲する**: 他の judge の守備範囲と重なった指摘は、担当 rubric に委ねて自分は書かない。分担表は [Runbook](../../docs/runbooks/review-agents.md)。
9. **judge はコードを変更しない**: 審査のみ。ファイル編集・commit・push・merge・deploy はしない。**merge / deploy のボタンを押すのは人間**。唯一の例外は qa-reviewer で、テストコードに限り Write 可 (`qa-rubric.md` 参照)。
