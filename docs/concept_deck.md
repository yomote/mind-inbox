---
marp: true
theme: default
paginate: true
size: 16:9
style: |
  :root {
    --bg: #f4f7fb;
    --surface: rgba(255, 255, 255, 0.9);
    --line: #d6e0ef;
    --text: #24324a;
    --muted: #5d6b82;
    --primary: #4653ff;
    --primary-soft: #eef0ff;
    --secondary: #00a6c7;
    --accent: #7c3aed;
    --shadow: 0 18px 44px rgba(36, 50, 74, 0.08);
  }
  section {
    font-family: "BIZ UDPGothic", "Hiragino Sans", "Yu Gothic", sans-serif;
    font-size: 22px;
    line-height: 1.38;
    letter-spacing: 0.01em;
    color: var(--text);
    padding: 54px 68px 52px;
    display: flex;
    flex-direction: column;
    justify-content: flex-start !important;
    align-items: stretch;
    background:
      radial-gradient(circle at top right, rgba(70, 83, 255, 0.13) 0%, rgba(70, 83, 255, 0) 28%),
      radial-gradient(circle at left bottom, rgba(0, 166, 199, 0.11) 0%, rgba(0, 166, 199, 0) 24%),
      linear-gradient(180deg, #fbfdff 0%, var(--bg) 100%);
    box-sizing: border-box;
    position: relative;
  }
  section::before {
    content: "";
    position: absolute;
    inset: 0 0 auto 0;
    height: 10px;
    background: linear-gradient(90deg, var(--primary) 0%, var(--accent) 52%, var(--secondary) 100%);
  }
  section::after {
    content: attr(data-marpit-pagination);
    position: absolute;
    right: 28px;
    bottom: 18px;
    width: 34px;
    height: 34px;
    border-radius: 999px;
    display: grid;
    place-items: center;
    font-size: 13px;
    font-weight: 700;
    color: var(--muted);
    background: rgba(255, 255, 255, 0.84);
    border: 1px solid rgba(93, 107, 130, 0.12);
  }
  section > * {
    position: relative;
    z-index: 1;
  }
  h1 {
    margin: 0 0 20px;
    font-size: 36px;
    line-height: 1.16;
    letter-spacing: -0.02em;
    color: #172033;
  }
  h2 {
    margin: 0 0 10px;
    font-size: 25px;
    color: #223150;
  }
  h3 {
    margin: 0 0 8px;
    font-size: 20px;
    color: #223150;
  }
  p, ul, ol {
    margin: 0.25em 0 0;
  }
  ul, ol {
    padding-left: 1.1em;
  }
  li {
    margin: 0.22em 0;
  }
  strong {
    color: #162033;
  }
  table {
    border-collapse: collapse;
    margin-top: 14px;
    font-size: 17px;
    background: rgba(255, 255, 255, 0.88);
    border-radius: 18px;
    overflow: hidden;
    box-shadow: var(--shadow);
  }
  th, td {
    border: 1px solid var(--line);
    padding: 11px 12px;
    vertical-align: top;
  }
  th {
    background: #edf2ff;
    color: #223150;
    text-align: left;
    font-weight: 700;
  }
  small {
    font-size: 13px;
    color: var(--muted);
  }
  .eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 7px 12px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid rgba(255, 255, 255, 0.56);
    color: var(--primary);
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    backdrop-filter: blur(10px);
  }
  .lead {
    color: #ffffff;
    background:
      radial-gradient(circle at top right, rgba(255, 255, 255, 0.16) 0%, rgba(255, 255, 255, 0) 24%),
      linear-gradient(135deg, #2837e8 0%, #4a46f0 48%, #00a6c7 100%);
    padding: 56px 72px;
    justify-content: flex-start !important;
  }
  section.lead::before,
  section.lead::after {
    display: none;
  }
  section.lead h1 {
   text-align: center;
    color: var(--primary);
  }
  section.lead h2 {
    text-align: center;
    color: var(--muted);
  }
  section.lead h3,
  section.lead strong,
  section.lead li,
  section.lead p {
    color: #ffffff;
  }
  section.lead h1 {
    font-size: 50px;
    margin-top: 18px;
    margin-bottom: 16px;
  }
  .hero {
    display: grid;
    grid-template-columns: 1.18fr 0.82fr;
    gap: 18px;
    align-items: stretch;
    margin-top: 22px;
  }
  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px;
    align-items: start;
  }
  .grid-3 {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 18px;
    align-items: start;
  }
  .card {
    background: var(--surface);
    border: 1px solid rgba(214, 224, 239, 0.92);
    border-radius: 24px;
    padding: 18px 20px;
    box-shadow: var(--shadow);
  }
  .card.accent {
    background: linear-gradient(135deg, rgba(70, 83, 255, 0.96) 0%, rgba(124, 58, 237, 0.92) 100%);
    border: 1px solid rgba(255, 255, 255, 0.16);
    box-shadow: 0 20px 50px rgba(14, 26, 89, 0.25);
  }
  .card.accent h1,
  .card.accent h2,
  .card.accent h3,
  .card.accent p,
  .card.accent li,
  .card.accent strong {
    color: #ffffff;
  }
  .card.primary {
    background: linear-gradient(180deg, #ffffff 0%, var(--primary-soft) 100%);
  }
  .card.highlight,
  .cell.highlight {
    border: 2px solid rgba(70, 83, 255, 0.5);
    box-shadow: 0 20px 48px rgba(70, 83, 255, 0.12);
  }
  .muted {
    color: var(--muted);
  }
  .statement {
    margin-top: 16px;
    padding: 16px 18px;
    border-left: 6px solid var(--primary);
    background: rgba(255, 255, 255, 0.72);
    border-radius: 0 18px 18px 0;
    font-size: 28px;
    line-height: 1.32;
    font-weight: 700;
    box-shadow: var(--shadow);
  }
  .tag-row {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 16px;
  }
  .tag {
    display: inline-flex;
    align-items: center;
    padding: 7px 12px;
    border-radius: 999px;
    background: rgba(70, 83, 255, 0.9);
    border: 1px solid rgba(255, 255, 255, 0.16);
    color: #ffffff;
    font-size: 14px;
    font-weight: 700;
  }
  .kpi {
    font-size: 42px;
    line-height: 1;
    font-weight: 800;
    color: var(--primary);
    margin-bottom: 8px;
  }
  .flow {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
    margin-top: 14px;
    align-items: start;
  }
  .step {
    min-height: 154px;
    padding: 16px;
    border-radius: 22px;
    border: 1px solid var(--line);
    background: rgba(255, 255, 255, 0.84);
    box-shadow: var(--shadow);
  }
  .step-no {
    font-size: 13px;
    font-weight: 700;
    color: var(--primary);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 10px;
  }
  .triple-note {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-top: 16px;
    align-items: start;
  }
  .pill {
    padding: 12px 14px;
    border-radius: 16px;
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid rgba(214, 224, 239, 0.92);
    text-align: center;
    font-size: 17px;
    font-weight: 700;
  }
  .compare {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px;
    align-items: start;
  }
  .quad {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
    margin-top: 14px;
    align-items: start;
  }
  .cell {
    min-height: 118px;
    padding: 16px 18px;
    border-radius: 20px;
    border: 1px solid var(--line);
    background: rgba(255, 255, 255, 0.82);
    box-shadow: var(--shadow);
  }
  .label {
    font-size: 13px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 8px;
  }
  .axis {
    display: flex;
    justify-content: space-between;
    margin-top: 10px;
    font-size: 14px;
    color: var(--muted);
  }
  .quote {
    margin-top: 14px;
    padding: 18px 20px;
    border-radius: 22px;
    background: linear-gradient(180deg, #ffffff 0%, #f4f0ff 100%);
    border: 1px solid rgba(124, 58, 237, 0.18);
    font-size: 25px;
    line-height: 1.38;
    font-weight: 700;
    box-shadow: var(--shadow);
  }
  .footnote {
    margin-top: 10px;
    font-size: 12px;
    color: var(--muted);
  }
  .center {
    text-align: center;
  }
  section.compact {
    font-size: 20px;
      padding: 0;
  }
  section.compact h1 {
    font-size: 36px;
    color: #fff;
    background: linear-gradient(135deg, rgba(70, 83, 255, 0.96) 0%, rgba(124, 58, 237, 0.92) 100%);
    border: 1px solid rgba(255, 255, 255, 0.16);
    box-shadow: 0 20px 50px rgba(14, 26, 89, 0.25);
    height: 100px;
    display: flex;
    align-items: center;
    line-height: 1;
    box-sizing: border-box;
    padding-left: 40px;
    padding-top: 10px;
  }
  section.compact> :not(h1) {
    margin: 30px;
  }
  section.compact .statement {
    font-size: 24px;
  }
  .final-message {
    margin-top: 24px;
    padding: 28px 34px;
    border-radius: 28px;
    background: linear-gradient(135deg, rgba(70, 83, 255, 0.95) 0%, rgba(124, 58, 237, 0.9) 58%, rgba(0, 166, 199, 0.92) 100%);
    color: #ffffff;
    box-shadow: 0 26px 56px rgba(70, 83, 255, 0.24);
  }
  .final-message h2,
  .final-message p,
  .final-message strong {
    color: #ffffff;
  }
---

<!-- markdownlint-disable MD001 MD022 MD032 MD036 MD041 MD060 -->

<!-- _class: lead -->

<div class="eyebrow">Mind Inbox / Executive Concept Deck</div>

# 対話型・自己理解アーティファクト生成アプリ

## 「会話で終わるAI体験」を、「自己理解が資産として育つ体験」へ変える

<div class="hero">
<div class="card accent">

### ひとことで
**話した内容が流れず、自己理解の地図として蓄積される。**  
AIとの対話を、継続的な自己理解の基盤へ転換するプロダクト。

</div>
<div class="card accent">

### 経営視点の要点
- AI相談アプリではない
- 継続利用が前提の「共同編集型アーティファクト」
- 低負荷なリアルタイム音声対話が入口

</div>
</div>

<div class="tag-row">
<div class="tag">会話 × 構造化</div>
<div class="tag">継続利用 × 再訪価値</div>
<div class="tag">自己理解データ資産</div>
</div>

<small>注：医療的な診断・治療ではなく、自己理解・整理・相談準備を支援するツールとして位置づける</small>

---

<!-- _class: compact -->

# 0. 企画概要

<div class="grid-3">
<div class="card primary">

### 仮コンセプト
**モヤモヤを話す** → **AIが構造化する** → **自己理解の地図として育つ**

</div>
<div class="card">

### プロダクトの位置づけ
**AI相談アプリ**ではなく、  
**自己理解アーティファクト共同編集ツール**

</div>
<div class="card">

### 主インターフェース
文字入力よりも、  
**低負荷なリアルタイム音声対話**を主軸に置く

</div>
</div>

<div class="statement">
狙う価値は、<strong>「その場で少し楽になる」</strong>だけではなく、<strong>「後から見返せる自己理解の資産が増える」</strong>こと。
</div>

---

<!-- _class: compact -->

# 1. 解決したい課題

<div class="grid-2">
<div class="card">

### いま起きていること
- ChatGPTに悩みを話すことはできる
- その場では気持ちが少し整理される
- ただし会話は流れ、**比較・再利用・横断分析**しづらい

</div>
<div class="card">

### その結果、起きること
- 同じ悩みを何度も話している
- 継続テーマや反応パターンが見えない
- 人に相談するときに再言語化コストが高い
- 記録は残っても**構造化された自己理解**にならない

</div>
</div>

<div class="quote">
「話せる」ことと「自己理解として蓄積される」ことの間に、まだ大きな空白がある。
</div>

---

<!-- _class: compact -->

# 2. 既存手段との違い

| 手段 | 強み | 弱み |
|---|---|---|
| ChatGPT | 深掘り対話がしやすい | 会話が流れる、構造化成果物が育たない |
| Notion / メモ / 日記 | 蓄積しやすい | 整理・構造化・深掘りを人間が担う必要がある |
| カウンセリング / コーチング | 深い対話、専門性 | 頻度・コスト・継続データ化に限界 |

<div class="statement">
本アプリは、<strong>対話による整理</strong>と<strong>構造化された蓄積</strong>を同時に成立させ、会話を<strong>編集可能な自己理解アーティファクト</strong>へ変換する。
</div>

---

<!-- _class: compact -->

# 3. コア体験

<div class="grid-3">
<div class="card">

### 1. 話す
- モヤモヤをそのまま吐き出せる
- 負荷の低い音声対話
- AIはまず受け止める

</div>
<div class="card primary">

### 2. AIが整理する
- 論点
- 感情
- 事実
- 身体状態
- 未解決の問い

</div>
<div class="card">

### 3. 形として残る
- テーママップ
- 自己理解カード
- 継続比較できる履歴

</div>
</div>

<div class="statement">
会話が主役ではない。<strong>会話によって育つアーティファクト</strong>が主役である。
</div>

---

<!-- _class: compact -->

# 4. システムの外部設計

## 処理フロー
<div class="flow">
<div class="step">
<div class="step-no">Step 01</div>
<h3>Dump</h3>
未整理なモヤモヤを、まずそのまま話す
</div>
<div class="step">
<div class="step-no">Step 02</div>
<h3>Structure</h3>
論点・感情・事実・問いを抽出し、見える形にする
</div>
<div class="step">
<div class="step-no">Step 03</div>
<h3>Clarify</h3>
最小限の質問で、曖昧さと抜けを減らす
</div>
<div class="step">
<div class="step-no">Step 04</div>
<h3>Artifactize</h3>
アーティファクトとして保存し、次回以降に引き継ぐ
</div>
</div>

<div class="triple-note">
<div class="pill">最優先は「解決」より「整理」</div>
<div class="pill">Plan提案は毎回必須にしない</div>
<div class="pill">望まれたときだけ次の一歩を出す</div>
</div>

---

<!-- _class: compact -->

# 5. 生成・蓄積されるアーティファクト

<div class="grid-3">
<div class="card">

### その場の価値
**セッション要約**
- いま何が起きているか
- 何が核心か
- 何が未整理か

</div>
<div class="card primary">

### 継続価値
**自己理解スナップショット**
- 継続テーマ
- 感情や身体状態
- 未解決の問い
- 仮説の履歴

</div>
<div class="card">

### 長期価値
**自己理解レポート / 思考傾向分析**
- テーマの推移
- よく出る反応パターン
- 早期サイン
- 効きやすい対処傾向

</div>

</div>

<div class="statement">
価値は単発の回答品質ではなく、<strong>時間とともに解像度が上がる自己理解データ</strong>にある。
</div>

---

<!-- _class: compact -->

# 6. なぜチャット単体では足りないのか

<div class="compare">
<div class="card">

### チャット
- 話す
- AIが返す
- その場では整理された感覚がある
- しかし履歴が**流れて終わりやすい**

</div>
<div class="card highlight">

### このアプリ
- 話す
- AIが構造化する
- 人間が修正する
- **構造物として残る**
- 履歴比較と再利用ができる

</div>
</div>

<div class="quote">
このプロダクトは、未整理な内面を対話によって編集可能なアーティファクトへ変換し、継続的に自己理解を育てるシステムである。
</div>

---

<!-- _class: compact -->

# 7. 対話者としての要件

<div class="grid-2">
<div class="card">

### 必須
- 誠実
- 境界線を守る
- 深く共感する
- 断定しすぎない
- 相手の視点を丁寧に扱う

</div>
<div class="card">

### 避ける
- 感情に巻き込まれすぎる
- 偉そうに結論を押しつける
- 安易に診断っぽく語る
- 毎回すぐ解決策に飛ぶ

</div>
</div>

<div class="statement">
本命は、<strong>心理的に疲れにくいリアルタイム音声対話</strong>。声・間・応答速度まで含めて「心をすり減らさない」設計が重要。
</div>

---

<!-- _class: compact -->

# 8. 対象ユーザー

<div class="grid-2">
<div class="card primary">

### 初期ターゲット
- 内省欲求が高い
- 日常的にAIへ思考ダンプしている
- でも会話が流れて不満がある
- 日記やフォーム入力は続かない
- テキストより**話して整理したい**

</div>
<div class="card">

### 具体像
**知的労働者で、内省欲求は高いが、記録・整理の継続が苦手な人**

<p class="muted">たとえば、日常的にAIへ相談するが、後から「何が繰り返し起きているのか」を見返せていない層。</p>

</div>
</div>

---

<!-- _class: compact -->

# 8-2. 初期市場の切り方

<div class="grid-3">
<div class="card">

### 広く取りにいかない
「悩みがある全員」を狙うと、訴求もプロダクトもぼやける

</div>
<div class="card highlight">

### 最初に狙う層
**AIで思考整理をしているが、蓄積と自己理解の更新に不満がある層**

</div>
<div class="card">

### 勝ち筋
回答品質競争ではなく、**継続的に育つ構造化体験**で差別化する

</div>
</div>

---

<!-- _class: compact -->

# 9. 市場性：追い風はあるか

<div class="grid-2 center">
<div class="card">
<div class="kpi">$7.48B</div>
2024年の世界メンタルヘルスアプリ市場規模

<div class="muted">2030年まで CAGR 14.6%</div>
</div>
<div class="card">
<div class="kpi">$1.71B</div>
2025年の世界 AI in Mental Health 市場規模

<div class="muted">2033年まで CAGR 23.29%</div>
</div>
</div>

<div class="triple-note">
<div class="pill">市場は成長領域にある</div>
<div class="pill">既存は単発会話・気分記録寄り</div>
<div class="pill">会話→自己理解資産化には余地がある</div>
</div>

<div class="footnote">出典：Grand View Research の市場レポートを基に整理</div>

---

<!-- _class: compact -->

# 10. 競争環境：どこにポジションを取るか

## 2軸での見取り図

<div class="quad">
<div class="cell">
<div class="label">会話起点 × 単発利用</div>
ChatGPT
</div>
<div class="cell highlight">
<div class="label">会話起点 × 継続蓄積</div>
<strong>本企画</strong><br>
会話から入り、継続的に自己理解アーティファクトを育てる
</div>
<div class="cell">
<div class="label">記録起点 × 単発利用</div>
単発メモ
</div>
<div class="cell">
<div class="label">記録起点 × 継続蓄積</div>
Notion / 日記
</div>
</div>

<div class="axis">
<span>← 会話起点</span>
<span>記録起点 →</span>
</div>
<div class="axis">
<span>↓ 単発利用</span>
<span>継続蓄積 ↑</span>
</div>

---

<!-- _class: compact -->

# 11. 市場価値仮説

## このプロダクトが埋める空白

<div class="grid-3">
<div class="card">

### Gap 1
**話せるが、蓄積されない**

</div>
<div class="card">

### Gap 2
**蓄積できても、構造化されない**

</div>
<div class="card highlight">

### Gap 3
**構造化されても、自己理解の更新につながらない**

</div>
</div>

<div class="statement">
これは「AI相談アプリ」ではなく、<strong>会話を継続的な自己理解アーティファクトへ変換する基盤</strong>である。
</div>

---

<!-- _class: compact -->

# 11-2. 市場価値仮説（補足）

<div class="grid-2">
<div class="card primary">

### 参考動向
AIジャーナリング系の Rosebud が 2025年にシードで 600万ドル調達。  
AI self-reflection / journaling 領域への投資関心は確認できる。

</div>
<div class="card">

### 含意
- 価値の重心は「会話」だけでなく「継続データ」に移っている
- 差別化の軸は回答品質そのものより、**構造化された蓄積体験**

</div>
</div>

---

<!-- _class: compact -->

# 12. 価値仮説KPI（ROIの代わりに何を見るか）

## ROIの前に、まず確かめるべきこと

<div class="grid-3">
<div class="card center">
<h3>継続率</h3>
<div class="kpi">1週 / 4週</div>
この体験が習慣化するか
</div>
<div class="card center">
<h3>整理実感</h3>
<div class="kpi">Post Session</div>
頭が軽くなる即時価値があるか
</div>
<div class="card center">
<h3>新規気づき率</h3>
<div class="kpi">Insight</div>
新しい見え方が生まれているか
</div>
</div>

---

<!-- _class: compact -->

# 12-2. 価値仮説KPI（後半）

<div class="grid-3">
<div class="card center">
<h3>再訪率</h3>
<div class="kpi">Artifact Return</div>
単なる会話で終わっていないか
</div>
<div class="card center">
<h3>採用率</h3>
<div class="kpi">Next Step</div>
行動や相談に接続できているか
</div>
<div class="card highlight center">
<h3>判断基準</h3>
<div class="kpi">3 Signals</div>
売上より先に、継続・再訪・気づきが立つか
</div>
</div>

---

<!-- _class: compact -->

# 13. MVP

<div class="grid-2">
<div class="card">

### 最初に必要なもの
- 左：セッションUI（チャット or 音声）
- 右：テーママップ / 自己理解カード
- セッション保存
- 過去アーティファクト一覧
- 週次の簡易レポート

</div>
<div class="card">

### 後回しにするもの
- 高度な統計ダッシュボード
- Notion等との本格連携
- 音声UIの高度最適化
- タスク自動同期

</div>
</div>

<div class="quote">
勝負ポイントは、<strong>「話したら、右側に自分の頭の中が形になっていく」</strong>という瞬間の体感価値。
</div>

---

<!-- _class: compact -->

# 14. 一言でいうと

<div class="grid-3">
<div class="card">話すことで、頭の中のモヤモヤを外に出す</div>
<div class="card primary">AIと一緒に“自己理解の地図”として形にする</div>
<div class="card">その地図を継続的に育てていく</div>
</div>

<div class="final-message">
<h2>企画の芯</h2>
<p><strong>相談のための会話</strong>ではなく、<strong>自己理解アーティファクトを共同編集する体験</strong>を作る。</p>
</div>
