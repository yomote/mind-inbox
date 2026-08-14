/**
 * tRPC エラーの **機械可読な識別子**。BFF とフロントが同じ 1 個のリテラルを見る。
 *
 * なぜ code だけでは足りないか (#82 / PR #416 Codex 4 巡目 P2):
 * tRPC は **procedure が見つからないときにも `NOT_FOUND`** を返す
 * (`No procedure found on path "consultation.approve"`)。つまり frontend / BFF の
 * 版ずれ・BFF 単独配備・ルーティング事故でも `NOT_FOUND` は飛んでくる。
 * `code === "NOT_FOUND"` だけで「承認レコードがもう無い」と読むと、**生きている
 * checkpoint を持つ承認カードを閉じてしまう** (承認も却下も再試行もできなくなる) —
 * ai-agent の 404 を detail で選り分けたのと同じ事故が、1 段上の層で起きる。
 *
 * だから BFF は「承認レコードがもう無い」ときだけこの token を `message` に載せ、
 * フロントは **code + この token の一致**で判定する。
 *
 * このファイルは **依存を持たない値だけ**を置く (フロントが値として import するため。
 * zod や Node の API を足すとフロントのバンドルに引きずり込まれる)。
 */

/**
 * `consultation.approve` の対象が ai-agent 側にもう無い (TTL 1h 失効 / 再起動で消えた /
 * approved・rejected 済み)。**再試行しても永久に成功しない**ので、フロントはカードを
 * 閉じて会話を進められる状態に戻す (dialogue-session.mdx §5.9)。
 *
 * 逆に、この token が付いていない `NOT_FOUND` は「承認レコードについて何も言っていない」
 * ので通信失敗と同じ扱い = カードを残す。
 */
export const APPROVAL_NOT_FOUND_TOKEN = "approval-not-found";
