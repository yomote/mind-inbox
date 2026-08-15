/**
 * zod の検証失敗を **機微データを載せずに** ログ / エラーメッセージへ落とすための要約 (#313)。
 *
 * ## なぜ専用の関数が要るか
 *
 * `ZodError.message` / `issue.message` は **弾かれた値そのもの**を含むことがある
 * (`Invalid enum value. Expected ... received 'なんとか'`)。ここで検証に掛かるのは
 * ai-agent が返した抽出結果や Cosmos のドキュメントで、中身は相談の本文 (最も機微なデータ) から
 * 作られている。そのままログに出すと Application Insights に相談内容が漏れる
 * (レビュー観点 S3 / 監査 E-2)。
 *
 * よって **場所 (path) と壊れ方の種別 (code) だけ**を出す。原因の切り分けにはこれで足り、
 * 値は要らない。
 */
import type { ZodError } from "zod";

/** 出力例: `items[0].mention.affect.intensity:too_big, newProblemCount:invalid_type` */
export function summarizeIssues(error: ZodError, maxIssues = 5): string {
  const issues = error.issues.slice(0, maxIssues).map((issue) => {
    const path = issue.path.length > 0 ? formatPath(issue.path) : "(root)";
    return `${path}:${issue.code}`;
  });
  const rest = error.issues.length - issues.length;
  return rest > 0 ? `${issues.join(", ")} (+${rest} more)` : issues.join(", ");
}

/** zod v4 の `issue.path` は `PropertyKey[]` (symbol を含みうる)。symbol は description を出す。 */
function formatPath(path: PropertyKey[]): string {
  return path
    .map((segment, i) =>
      typeof segment === "number"
        ? `[${segment}]`
        : i === 0
          ? String(segment)
          : `.${String(segment)}`,
    )
    .join("");
}
