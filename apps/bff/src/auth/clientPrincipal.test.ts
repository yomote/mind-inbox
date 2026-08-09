/**
 * [L1] EasyAuth の `x-ms-client-principal` → userId 解決。
 *
 * 無いと何が静かに通るか: 全ユーザーのデータが同じパーティション (`local`) に混ざる、
 * あるいは壊れたヘッダで 500 になる。userId は Cosmos のパーティションキーなので、
 * ここの間違いは「他人のデータが見える」か「保存先が毎回変わる」に直結する。
 */
import { describe, expect, it } from "vitest";
import {
  LOCAL_USER_ID,
  resolveUserId,
  userIdFromClientPrincipalHeader,
} from "./clientPrincipal";

function encode(principal: unknown): string {
  return Buffer.from(JSON.stringify(principal), "utf8").toString("base64");
}

function reqWith(headers: Record<string, string>) {
  return { headers: { get: (name: string) => headers[name] ?? null } };
}

describe("[L1] userIdFromClientPrincipalHeader", () => {
  it("oid クレーム (長い URI 形式) を userId にする", () => {
    const header = encode({
      auth_typ: "aad",
      claims: [
        { typ: "http://schemas.microsoft.com/identity/claims/objectidentifier", val: "oid-123" },
        { typ: "preferred_username", val: "someone@example.com" },
      ],
    });
    expect(userIdFromClientPrincipalHeader(header)).toBe("oid-123");
  });

  it("短い oid 形式も拾う", () => {
    const header = encode({ claims: [{ typ: "oid", val: "oid-short" }] });
    expect(userIdFromClientPrincipalHeader(header)).toBe("oid-short");
  });

  it("oid が無ければ sub にフォールバックする", () => {
    const header = encode({ claims: [{ typ: "sub", val: "sub-999" }] });
    expect(userIdFromClientPrincipalHeader(header)).toBe("sub-999");
  });

  it("ヘッダが無ければ local", () => {
    expect(userIdFromClientPrincipalHeader(undefined)).toBe(LOCAL_USER_ID);
    expect(userIdFromClientPrincipalHeader(null)).toBe(LOCAL_USER_ID);
    expect(userIdFromClientPrincipalHeader("")).toBe(LOCAL_USER_ID);
  });

  it("base64 でも JSON でもない値で落ちず local になる", () => {
    expect(userIdFromClientPrincipalHeader("not-a-base64-json!!!")).toBe(LOCAL_USER_ID);
  });

  it("claims が空でも local", () => {
    expect(userIdFromClientPrincipalHeader(encode({ claims: [] }))).toBe(LOCAL_USER_ID);
  });
});

describe("[L1] resolveUserId", () => {
  it("リクエストヘッダから解決する", () => {
    const req = reqWith({
      "x-ms-client-principal": encode({ claims: [{ typ: "oid", val: "oid-req" }] }),
    });
    expect(resolveUserId(req)).toBe("oid-req");
  });

  it("headers を持たない req (テスト用ダミー) でも落ちない", () => {
    expect(resolveUserId(undefined)).toBe(LOCAL_USER_ID);
    expect(resolveUserId({} as never)).toBe(LOCAL_USER_ID);
  });
});
