#!/usr/bin/env node
/**
 * `apps/bff` を pnpm 以外のパッケージマネージャで install させないガード (#420)。
 *
 * なぜ必要か: このディレクトリは長く npm 管理だったため、エージェントも人間も
 * 反射で `npm install` を打つ。打てても**その場では何も壊れない** — `package-lock.json`
 * が新しく生えて、`pnpm-lock.yaml` と二重管理になり、CI の `--frozen-lockfile` か
 * デプロイの node_modules で初めて壊れる。「壊れてから気づく」類なので機構で止める。
 * (2026-08-14 に逆向きの取り違え — npm 管理下への `pnpm-lock.yaml` 誤コミット — が
 *  実際に起きている: PR #416)
 *
 * 依存ゼロで書く: preinstall は node_modules が無い状態で走る。
 */

import { pathToFileURL } from "node:url";

/**
 * npm 系 CLI が渡す `npm_config_user_agent` から pnpm 起動かを判定する。
 *
 * 値の例:
 *   pnpm: "pnpm/9.15.0 npm/? node/v22.22.2 linux x64"
 *   npm : "npm/10.9.7 node/v22.22.2 linux x64 workspaces/false"
 *
 * **未設定は「不明」であって「合格」ではない** — 判定できなかったものを通すと、
 * ガードがあるのに素通りする状態を誰も検知できない。false を返して弾く。
 *
 * @param {string | undefined} userAgent
 * @returns {boolean}
 */
export function isPnpmAgent(userAgent) {
  return typeof userAgent === "string" && userAgent.startsWith("pnpm/");
}

/** 弾いたときに出すメッセージ。 */
export const DENY_MESSAGE = [
  "",
  "  apps/bff は pnpm 管理です (#420 で npm から移行)。",
  "  `npm install` / `npm ci` を使うと package-lock.json が生えて pnpm-lock.yaml と二重管理になります。",
  "",
  "    pnpm install                       # apps/bff の中で",
  "    pnpm --dir apps/bff install        # リポジトリ root から",
  "",
].join("\n");

const isEntry = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isEntry && !isPnpmAgent(process.env.npm_config_user_agent)) {
  process.stderr.write(
    `ERROR: apps/bff は pnpm でしか install できません (検出: ${process.env.npm_config_user_agent ?? "<npm_config_user_agent が未設定>"})\n${DENY_MESSAGE}`,
  );
  process.exit(1);
}
