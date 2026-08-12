/**
 * **外部から入ってくる入力の大きさの上限** (BFF の唯一の定義場所 / #313 C-1)。
 *
 * ## なぜ必要か
 *
 * BFF の入力 zod スキーマには長さ上限が 1 つも無かった。tRPC / HTTP の本文は
 * Functions の既定 (数十 MB) まで受け付けられ、`consultation.extract` に巨大な文字列や
 * 大量のメッセージを詰めると **そのまま Azure OpenAI / VOICEVOX の課金として燃える**
 * (会話全文はプロンプトに切り詰めなしで連結される)。認証済みの 1 アカウントで青天井に
 * 燃やせる状態で、受け皿は事後に飛ぶ予算アラートしか無かった。
 *
 * サーバ側レート制限 (#313 F-1) はまだ無い。**「1 リクエストあたりの最大コスト」を
 * 有限にする**のがここの役割で、レート制限の代わりにはならない。
 *
 * ## 値の決め方 (根拠)
 *
 * すべて「実際の使われ方の 3〜10 倍」に置く — 正常な操作を絶対に切らないことを優先し、
 * それでも青天井との差を 4 桁縮める、という配分。上限に当たったら 400 (zod) が返るので
 * 静かに切り詰められることはない (切り詰めは「言ったのに残っていない」を生むので採らない)。
 */

/**
 * 識別子 (sessionId / problemId / mentionId / approvalRequestId) の最大長。
 *
 * 実体は UUID v4 (36) と `prob-stub-<uuid>` (46)。ID はログ行やクエリパラメータに
 * そのまま載るので、実長の 4 倍で頭打ちにしておく。
 */
export const MAX_ID_LENGTH = 200;

/**
 * 発話 1 通 (ユーザー入力 / 会話 1 メッセージ) の最大文字数。
 *
 * 入力の主経路は音声 (STT) で、日本語の発話速度は約 300〜400 字/分。8,000 字は
 * **1 通の中で 20 分以上しゃべり続けた場合**に相当し、実際の 1 発話 (数十〜数百字) の
 * 10 倍以上の余裕がある。キーボード入力なら更に届かない。
 */
export const MAX_MESSAGE_LENGTH = 8_000;

/**
 * `consultation.extract` に渡せる会話メッセージ数の上限。
 *
 * 1 セッション = 1 回の吐き出しで、実際は 5〜30 往復。200 件 = 100 往復は
 * その 3〜10 倍にあたる。
 */
export const MAX_CONVERSATION_MESSAGES = 200;

/**
 * `consultation.extract` に渡せる会話全文 (全メッセージの合計) の最大文字数。
 *
 * 件数 × 1 通の上限 (200 × 8,000 = 160 万字) は積として大きすぎるので、
 * **プロンプトに実際に載る総量**を別に締める。日本語はおおむね 1 トークン ≒ 1 字強なので
 * 120,000 字はおよそ 8 万トークン級 = 1 リクエストの入力コストの天井。
 * 実セッションの全文は数千〜2 万字程度なので、6 倍以上の余裕がある。
 */
export const MAX_CONVERSATION_TOTAL_CHARS = 120_000;

/**
 * `consultation.extract` の `draft.items` に渡せる件数の上限。
 *
 * 確定時に再抽出しない commit 経路 (#283 / ADR 0039 D1/D3) では、クライアントが
 * 表示中の preview 結果をそのまま送り返して永続化する。**この配列はそのまま
 * Cosmos への書き込み件数になる**ので、締めないと 1 リクエストで無制限に書ける。
 *
 * 1 セッションの吐き出しから採れる困りごとは実際は数件〜十数件。抽出元の会話が
 * 最大 200 件である以上、そこから生まれる item がそれを超えることは無いため、
 * 会話件数と同じ 200 を天井に置く。
 */
export const MAX_EXTRACTED_ITEMS = 200;

/**
 * draft の 1 item に載る本文 (`mention.statement` / `mention.excerpt`) の最大文字数。
 *
 * **件数だけ締めても中身が青天井なら意味がない** (PR #324 Codex 指摘 P2)。
 * `statement` は正規化済みの困りごと 1 文 (実際は 30〜150 字)、`excerpt` は元発話からの
 * 引用 (実際は数十〜数百字)。どちらも 2,000 字は 10 倍以上の余裕がある。
 * 1 発話の上限 (`MAX_MESSAGE_LENGTH` = 8,000) より小さいのは、引用は発話の一部だから。
 */
export const MAX_EXTRACTED_TEXT_LENGTH = 2_000;

/**
 * 感情ラベル (`mention.affect.label`) の最大文字数。「不安」「焦り」「安堵」のような語。
 */
export const MAX_AFFECT_LABEL_LENGTH = 100;

/** 自由タグ 1 個の最大文字数 (「転職」「родители」等の短い語を想定)。 */
export const MAX_TAG_LENGTH = 100;

/** 1 item に付けられる自由タグの最大個数 (実際は 1〜3 個)。 */
export const MAX_TAGS_PER_ITEM = 20;

/**
 * タイムスタンプ文字列 (`mention.createdAt`) の最大文字数。
 *
 * ISO 8601 (`2026-08-12T09:00:00.000Z`) は 24〜35 字。形式そのものは検証していないので
 * (既存ドキュメントの表記ゆれを弾かないため)、長さだけ頭打ちにする。
 */
export const MAX_TIMESTAMP_LENGTH = 64;

/**
 * draft 全体 (全 item の文字列の合計) の最大文字数。
 *
 * 件数 × 1 件の上限 (200 × 約 4,000 字) は積として大きすぎるので、会話全文と同じ考え方で
 * **合計**を別に締める (`MAX_CONVERSATION_TOTAL_CHARS` と同じ発想)。
 *
 * 値を会話全文の上限と同じにしているのは根拠がそこにあるため — draft は 1 セッションの
 * 会話から抽出された結果なので、**元の会話全文より長くなることは無い**。
 */
export const MAX_DRAFT_TOTAL_CHARS = MAX_CONVERSATION_TOTAL_CHARS;

/**
 * TTS (`POST /api/tts`) に渡せるテキストの最大文字数。
 *
 * 読み上げ対象は ai-agent の応答 1 通で、生成側が `max_tokens=1024`
 * (`apps/services/ai-agent/app/agents.py`) なので実際は 1,000〜1,500 字程度に収まる。
 * VOICEVOX は CPU バウンドで、文数がそのまま合成回数になる (`tts/ttsService.ts`) ため、
 * ここは「1 リクエストが占有できる CPU 時間」の上限でもある。
 */
export const MAX_TTS_TEXT_LENGTH = 8_000;

/**
 * Problem のタイトル (`problem.triage` の editTitle) の最大文字数。
 *
 * 一覧の 1 行見出しで、自動生成 (`domain/title.ts`) は 30 字前後。手で書き換える場合の
 * 余裕を見て 200 字。
 */
export const MAX_TITLE_LENGTH = 200;
