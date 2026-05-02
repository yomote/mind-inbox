/**
 * lint-staged 設定。pre-commit から `npx lint-staged` で起動される。
 *
 * 前提:
 *  - apps/bff: npm install 済み（ESLint）
 *  - apps/frontend: pnpm install 済み（ESLint）
 *  - ruff: グローバル or PATH 上にあること（推奨: `uv tool install ruff`）
 *  - shellcheck: 任意。インストール済みなら .sh をチェック、なければスキップ
 */
const { execSync } = require("node:child_process");
const path = require("node:path");

const REPO_ROOT = __dirname;

const hasCommand = (cmd) => {
  try {
    execSync(`command -v ${cmd}`, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
};

/** lint-staged は絶対パスを渡してくる。指定 dir 配下からの相対パスを返す。 */
const relTo = (dir, files) =>
  files
    .map((f) => path.relative(path.join(REPO_ROOT, dir), f))
    .map((p) => `"${p}"`)
    .join(" ");

/** 絶対パスを repo root からの相対パスに変換（prettier など repo root から実行するコマンド用）。 */
const relToRoot = (files) =>
  files
    .map((f) => path.relative(REPO_ROOT, f))
    .map((p) => `"${p}"`)
    .join(" ");

module.exports = {
  "apps/bff/**/*.ts": (files) => [
    `bash -c 'cd apps/bff && npx eslint --fix ${relTo("apps/bff", files)}'`,
    `prettier --write ${relToRoot(files)}`,
  ],

  "apps/frontend/**/*.{ts,tsx}": (files) => [
    `bash -c 'cd apps/frontend && npx eslint --fix ${relTo("apps/frontend", files)}'`,
    `prettier --write ${relToRoot(files)}`,
  ],

  "apps/services/**/*.py": (files) => {
    if (!hasCommand("ruff")) {
      console.warn(
        "[lint-staged] ruff が PATH 上に見つかりません。`uv tool install ruff` でインストールしてください。Python ファイルのチェックはスキップします。",
      );
      return [];
    }
    const list = relToRoot(files);
    return [`ruff check --fix ${list}`, `ruff format ${list}`];
  },

  "**/*.md": (files) => {
    const list = relToRoot(files);
    return [`prettier --write ${list}`, `markdownlint-cli2 --fix ${list}`];
  },

  "**/*.{json,yaml,yml}": "prettier --write",

  "**/*.sh": (files) => {
    if (!hasCommand("shellcheck")) {
      console.warn(
        "[lint-staged] shellcheck が見つかりません。`apt install shellcheck` などでインストールするとシェルスクリプトもチェックされます。",
      );
      return [];
    }
    return `shellcheck ${relToRoot(files)}`;
  },

  "cicd/iac/**/*.bicep": (files) => {
    if (!hasCommand("az")) {
      console.warn(
        "[lint-staged] az CLI が見つかりません。Bicep のチェックをスキップします。",
      );
      return [];
    }
    return files.map(
      (f) =>
        `bash -c 'az bicep build --file "${path.relative(REPO_ROOT, f)}" --stdout > /dev/null'`,
    );
  },
};
