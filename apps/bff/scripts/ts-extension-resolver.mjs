/**
 * `--experimental-strip-types` で BFF の `src/**.ts` を直接 import するための resolve hook。
 *
 * BFF の tsconfig は `module: commonjs` なので、ソースの相対 import は拡張子を持たない
 * (`import { ... } from "../trpc/domain"`)。Node の ESM 解決は拡張子を要求するため、
 * そのままでは contract-check から src を辿れない (ERR_MODULE_NOT_FOUND)。
 * ここで「解決に失敗した相対指定は `.ts` / `/index.ts` を足して再試行」するだけの
 * 最小の hook を挟む。テストハーネス専用で、ランタイム (Functions) には影響しない。
 */

/** @type {import("node:module").ResolveHook} */
export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (err) {
    if (!specifier.startsWith(".") || /\.[cm]?[jt]sx?$/.test(specifier)) throw err;
    for (const candidate of [`${specifier}.ts`, `${specifier}/index.ts`]) {
      try {
        return await nextResolve(candidate, context);
      } catch {
        // 次の候補へ
      }
    }
    throw err;
  }
}
