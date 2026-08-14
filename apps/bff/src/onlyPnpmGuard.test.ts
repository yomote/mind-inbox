import { describe, expect, it } from "vitest";

import { isPnpmAgent } from "../scripts/only-pnpm.mjs";

/**
 * 無いと何が静かに通るか: `apps/bff` で `npm install` が通り、`package-lock.json` が
 * 生えて `pnpm-lock.yaml` と二重管理になる (#420)。判定を「pnpm 以外も true」に
 * 緩めても preinstall は成功したままなので、install の成否だけでは気づけない。
 *
 * 仕様: `scripts/only-pnpm.mjs` は `npm_config_user_agent` が `pnpm/` で始まる
 * ときだけ install を許す。判定できない (未設定 / 非文字列) は**許さない**。
 */
describe("isPnpmAgent", () => {
  it("pnpm の user agent は許す", () => {
    expect(isPnpmAgent("pnpm/9.15.0 npm/? node/v22.22.2 linux x64")).toBe(true);
  });

  it("npm の user agent は弾く", () => {
    expect(isPnpmAgent("npm/10.9.7 node/v22.22.2 linux x64 workspaces/false")).toBe(false);
  });

  it("yarn / bun の user agent も弾く", () => {
    expect(isPnpmAgent("yarn/1.22.22 npm/? node/v22.22.2 linux x64")).toBe(false);
    expect(isPnpmAgent("bun/1.1.0 npm/? node/v22.22.2 linux x64")).toBe(false);
  });

  it("未設定は「不明」であって合格ではない", () => {
    expect(isPnpmAgent(undefined)).toBe(false);
  });

  it("pnpm を名前の一部に含むだけの偽装は弾く", () => {
    expect(isPnpmAgent("notpnpm/1.0.0 node/v22.22.2")).toBe(false);
  });
});
