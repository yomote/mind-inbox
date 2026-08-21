"""[単体] bootstrap.sh の分岐を、az / terraform / curl をスタブして振る舞いで固定する。

無いと何が静かに通るか:
    1. **pem の漏えい** — `az keyvault secret set` は既定でシークレット値を含む JSON を
       標準出力に返す。`--query id` の 1 行が消えても機能は壊れず (格納は成功する)、
       pem が端末とログに出るだけなので、目視でも実行でも気づけない。ここでは az
       スタブが実物と同じく「--query id が無ければ値入り JSON を返す」ため、この
       1 行が消えると漏えい検査が落ちる。
    2. **fail-closed の崩れ** — 途中失敗で後続 (KV 格納 / terraform) が走らないこと、
       「何が済んで何が済んでいないか」の台帳が出ることは、正常系だけ叩いていても
       壊れたことに気づけない。
    3. **#387 の迂回** — plan に差分があるのに apply してしまう回帰。

az / terraform / curl 本体は検証しない (それは各ツールの仕事)。固定するのは
bootstrap.sh の判断と、資格情報の取り回しだけ。

## スタブは「実応答の形」を写す (#499 発見 3)

**スタブがスクリプトの期待値を echo する作りにしてはいけない。** 以前の az スタブは
`keyvault key show` に対して query を解釈せず `false` / `true` を返していたため、
スクリプト側の JMESPath が誤っていても (`key.exportable` — 実際は `attributes` の下)
**構造的に検出できなかった** (PO の実実行で初めて露見 / #499 発見 2)。

いまの az スタブは**実 az の応答 JSON を持ち、スクリプトが渡した `--query` を実際に
評価する** (`tsv_query`)。したがって query を誤ったパスに戻すと、鍵の検証は
「エクスポート可能な鍵を見逃す」形でテストが赤くなる。

写せていない経路は**そう書いておく** — 検証 6c の `az rest` は query にパイプと関数
(`| length(@)`) を含み簡易評価器では写せないので、いまも固定値を返す。ここを
「実応答を写している」と読まないこと。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from check_key_vault import (
    FIELD_SEPARATOR,
    DataPlaneReport,
    classify_probe,
    data_plane_verdict,
    key_export_verdict,
)
from check_permissions import PermissionReport, compare_permissions

KIT_DIR = Path(__file__).resolve().parent
SCRIPT = KIT_DIR / "bootstrap.sh"
README = KIT_DIR / "README.md"
CHECK_PERMISSIONS = KIT_DIR / "check_permissions.py"
CHECK_KEY_VAULT = KIT_DIR / "check_key_vault.py"

# 想定する権限集合。**bootstrap.sh から読み込まずに直書きする** — シェル側を読んで
# しまうと、シェルの EXPECTED_PERMISSIONS を書き換えたときに期待値も一緒に動いて
# しまい、「想定が黙って広がった」を検出できなくなる。両者の一致は
# test_単体_シェルの想定権限集合が単体テストの期待値と一致する が見る。
EXPECTED = {"administration": "write", "metadata": "read"}

# ---------------------------------------------------------------------------
# スタブ — 呼び出しごとに argv と env を記録ディレクトリへ残す。
# テストは記録を読んで「何が呼ばれた / 呼ばれなかった / 何が渡った」を検査する。
# ---------------------------------------------------------------------------

RECORD_SNIPPET = """
rec="$STUB_RECORD_DIR/{tool}.$$.$RANDOM"
printf '%s\\n' "$@" > "$rec.argv"
env > "$rec.env"
"""

AZ_STUB = (
    "#!/usr/bin/env bash\n" + RECORD_SNIPPET.format(tool="az") + r"""
args="$*"

# スクリプトが渡した --query を取り出す (実 az と同じく、無ければ応答全体)。
query=""; prev=""
for a in "$@"; do
  [ "$prev" = "--query" ] && query="$a"
  prev="$a"
done

# 実応答の JSON に対して**スクリプト側の query を実際に評価する**簡易 JMESPath。
# ⚠️ ここが「スタブが実応答の形を写す」の要 (#499 発見 3)。期待値を echo する作りに
#    戻すと、JMESPath の誤り (attributes.exportable を key.exportable と書く等) が
#    構造的に検出できなくなる。
# 対応するのはドット区切りのパスだけ。存在しないパスは実 az と同じく空行を返す。
# bool の表記 (true / True) は az の版に依存するため STUB_AZ_TSV_BOOL で切り替える。
tsv_query() { # $1: 応答 JSON, $2: query
  STUB_TSV_BOOL="${STUB_AZ_TSV_BOOL:-lower}" python3 -c '
import json, os, sys
value = json.loads(sys.argv[1])
query = sys.argv[2]
if query:
    for part in query.split("."):
        value = value.get(part) if isinstance(value, dict) else None
        if value is None:
            break
if value is None:
    print("")
elif isinstance(value, bool):
    print(str(value).lower() if os.environ["STUB_TSV_BOOL"] == "lower" else str(value))
elif isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(value)
' "$1" "$2"
}

# ⚠️ case は上から順に最初の一致だけが効く。"deployment group create" は
#    "group create" にも部分一致するので、長いパターンを必ず先に置く。
case "$args" in
  *"account show"*)
    [ "${STUB_AZ_ACCOUNT:-ok}" = "fail" ] && { echo "Please run 'az login'" >&2; exit 1; }
    case "$args" in
      *"--query id -o tsv"*|*"--query id "*) echo "sub-0000" ;;
      *) echo '{"name": "stub-subscription", "id": "sub-0000"}' ;;
    esac ;;
  *"bicep build"*) : ;;
  *"deployment group what-if"*) echo "(what-if stub: + kv-dev-mindbox ほか)" ;;
  *"deployment group create"*)
    [ "${STUB_AZ_DEPLOY:-ok}" = "fail" ] && { echo "InvalidTemplateDeployment" >&2; exit 1; }
    echo "Succeeded" ;;
  *"group create"*) echo "Succeeded" ;;
  *"resource list"*)
    if [ "${STUB_AZ_RESOURCE:-ok}" = "untagged" ]; then
      echo '[{"name":"kv-dev-mindbox","tags":{"mindInboxLayer":"management"}},{"name":"stray-storage","tags":null}]'
    else
      echo '[{"name":"kv-dev-mindbox","tags":{"mindInboxLayer":"management"}},{"name":"stdevmindboxbak","tags":{"mindInboxLayer":"management"}}]'
    fi ;;
  *"keyvault key list"*|*"keyvault secret list"*)
    # データプレーン権限の事前確認 (手順 5) のプローブ。実測 (PO / 2026-08-17) の
    # 失敗はこの形 — Assignment: (not found) = ロールが 1 つも割り当たっていない。
    case "$args" in *"keyvault key list"*) probe="keys" ;; *) probe="secrets" ;; esac
    mode="${STUB_KV_DATAPLANE:-ok}"
    if [ "$mode" = "error" ]; then
      # 権限ではない失敗 (Vault 名の間違い / 到達不能)。ロール付与を案内してはいけない側。
      echo "ERROR: (VaultNotFound) Vault not found: kv-dev-mindbox" >&2; exit 1
    fi
    if [ "$mode" = "forbidden" ] || [ "$mode" = "$probe-forbidden" ]; then
      cat >&2 <<STUBERR
ERROR: (ForbiddenByRbac) Caller is not authorized to perform action on resource.
If role assignments, deny assignments or role definitions were changed recently, please observe propagation time.
Action: Microsoft.KeyVault/vaults/$probe/read
Assignment: (not found)
Vault: kv-dev-mindbox;location=japaneast
STUBERR
      exit 1
    fi
    : ;; # 通常時は -o none なので何も出さない
  *"keyvault key show"*)
    # 実 az の応答をそのまま写す。**エクスポート不可の鍵は attributes に exportable を
    # 持たない** (省略が既定) — これが実物の形で、`key.exportable` は**どちらのモード
    # でも空**になる (#499 発見 2 の再現。誤った query に戻すと exportable 側が素通りする)。
    if [ "${STUB_AZ_KEY:-ok}" = "exportable" ]; then
      body='{"attributes":{"created":1755400000,"enabled":true,"exportable":true,"recoverableDays":90,"recoveryLevel":"Recoverable+Purgeable","updated":1755400000},"key":{"e":"AQAB","key_ops":["decrypt","encrypt","sign","unwrapKey","verify","wrapKey"],"kid":"https://kv-dev-mindbox.vault.azure.net/keys/e2e-artifacts/stubkeyversion1","kty":"RSA","n":"c3R1Yi1wdWJsaWMtbW9kdWx1cw"},"managed":null,"tags":null}'
    else
      body='{"attributes":{"created":1755400000,"enabled":true,"recoverableDays":90,"recoveryLevel":"Recoverable+Purgeable","updated":1755400000},"key":{"e":"AQAB","key_ops":["decrypt","encrypt","sign","unwrapKey","verify","wrapKey"],"kid":"https://kv-dev-mindbox.vault.azure.net/keys/e2e-artifacts/stubkeyversion1","kty":"RSA","n":"c3R1Yi1wdWJsaWMtbW9kdWx1cw"},"managed":null,"tags":null}'
    fi
    tsv_query "$body" "$query" ;;
  *"rest --method get"*)
    # ⚠️ ここは query を評価していない (`... | length(@)` はパイプと関数を含み、
    #    上の簡易評価器では写せない)。**この経路のスタブは実応答の形を写していない**。
    [ "${STUB_AZ_BUDGET:-ok}" = "absent" ] && { echo "BudgetNotFound" >&2; exit 1; }
    echo "1" ;;
  *"keyvault secret show"*)
    # 実応答は value (pem 本文) を含む。ここでは本文の代わりに目印を置き、
    # --query tags.sha256 の絞りを外す変異が漏えい検査で落ちるようにしてある。
    case "${STUB_AZ_SECRET_SHOW:-absent}" in
      match|different)
        if [ "${STUB_AZ_SECRET_SHOW}" = "match" ]; then sha="${STUB_PEM_SHA:?}"; else sha="deadbeefdeadbeef"; fi
        body="{\"attributes\":{\"created\":1755400000,\"enabled\":true},\"contentType\":null,\"id\":\"https://kv-dev-mindbox.vault.azure.net/secrets/github-app-mgmt-private-key/stubversion0\",\"name\":\"github-app-mgmt-private-key\",\"tags\":{\"app-id\":\"12345\",\"purpose\":\"github-settings-mgmt-terraform\",\"sha256\":\"$sha\"},\"value\":\"STUB_SECRET_VALUE_PEM\"}"
        tsv_query "$body" "$query" ;;
      *) echo "SecretNotFound" >&2; exit 3 ;;
    esac ;;
  *"keyvault secret set"*)
    [ "${STUB_AZ_SECRET_SET:-ok}" = "forbidden" ] && {
      echo "ForbiddenByRbac: Caller is not authorized to perform action" >&2; exit 1; }
    # 実物の az と同じ振る舞いを模す: --file の中身を value に含む JSON を返し、
    # --query id が付いているときだけ id に絞る。この模倣が無いと
    # 「--query id を消しても漏えい検査が緑」になる (このテストの存在理由 1)。
    file=""; prev=""; has_query_id=false
    for a in "$@"; do
      [ "$prev" = "--file" ] && file="$a"
      [ "$prev" = "--query" ] && [ "$a" = "id" ] && has_query_id=true
      prev="$a"
    done
    sid="https://kv-dev-mindbox.vault.azure.net/secrets/github-app-mgmt-private-key/stubversion1"
    if $has_query_id; then
      echo "$sid"
    else
      printf '{"id": "%s", "value": %s}\n' "$sid" "$(python3 -c 'import json,sys; print(json.dumps(open(sys.argv[1]).read()))' "$file")"
    fi ;;
  *"keyvault show"*) echo "/subscriptions/sub-0000/resourceGroups/rg-mgmt-mindbox/providers/Microsoft.KeyVault/vaults/kv-dev-mindbox" ;;
  *) echo "az stub: 未対応の呼び出し: $args" >&2; exit 9 ;;
esac
exit 0
"""
)

CURL_STUB = (
    "#!/usr/bin/env bash\n" + RECORD_SNIPPET.format(tool="curl") + r"""
out=""; prev=""; url=""
for a in "$@"; do
  [ "$prev" = "-o" ] && out="$a"
  prev="$a"
  case "$a" in http*://*) url="$a" ;; esac
done
code=200
case "$url" in
  */app)
    if [ "${STUB_GH_APP:-ok}" = "mismatch" ]; then
      echo '{"id": 99999, "slug": "someone-elses-app"}' > "$out"
    else
      echo "{\"id\": ${STUB_APP_ID:-12345}, \"slug\": \"mind-inbox-settings-mgmt\"}" > "$out"
    fi ;;
  */app/installations\?per_page*)
    if [ "${STUB_GH_INSTALLS:-ok}" = "none" ]; then
      echo '[]' > "$out"
    else
      echo '[{"id": 42, "account": {"login": "yomote"}}]' > "$out"
    fi ;;
  */app/installations/42/access_tokens)
    code=201
    # permissions は App の実権限がそのまま返る欄。手動フォーム作成 (#497) では
    # ここが「2 つに絞れているか」を機械が見られる唯一の場所なので、下限割れ
    # (noadmin) だけでなく余分 (extra) / 不足 (nometa) も再現する。
    case "${STUB_GH_TOKEN:-ok}" in
      noadmin) perms='{"administration": "read", "metadata": "read"}' ;;
      extra)   perms='{"administration": "write", "metadata": "read", "contents": "read", "actions": "write"}' ;;
      nometa)  perms='{"administration": "write"}' ;;
      *)       perms='{"administration": "write", "metadata": "read"}' ;;
    esac
    echo "{\"token\": \"ghs_STUBTOKEN_SECRET\", \"permissions\": $perms}" > "$out" ;;
  *) echo "curl stub: 未対応 URL: $url" >&2; exit 9 ;;
esac
printf '%s' "$code"
"""
)

TERRAFORM_STUB = (
    "#!/usr/bin/env bash\n" + RECORD_SNIPPET.format(tool="terraform") + r"""
sub=""
for a in "$@"; do
  case "$a" in init|plan|apply) sub="$a"; break ;; esac
done
case "$sub" in
  init) echo "Terraform has been successfully initialized!" ;;
  plan)
    case "${STUB_TF_PLAN:-importonly}" in
      importonly) echo "Plan: 4 to import, 0 to add, 0 to change, 0 to destroy." ;;
      drift)      echo "Plan: 4 to import, 0 to add, 1 to change, 0 to destroy." ;;
      nosummary)  echo "(plan output without a summary line)" ;;
      fail)       echo "Error: GET https://api.github.com/...: 403" >&2; exit 1 ;;
    esac ;;
  apply) echo "Apply complete! Resources: 4 imported, 0 added, 0 changed, 0 destroyed." ;;
  *) echo "terraform stub: 未対応: $*" >&2; exit 9 ;;
esac
exit 0
"""
)


@pytest.fixture()
def kit(tmp_path: Path):
    """スタブ入り PATH と本物の RSA 鍵 (捨て鍵) を用意する。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in [("az", AZ_STUB), ("curl", CURL_STUB), ("terraform", TERRAFORM_STUB)]:
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)

    record = tmp_path / "record"
    record.mkdir()

    pem = tmp_path / "app.private-key.pem"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
         "-out", str(pem)],
        check=True, capture_output=True,
    )
    pem.chmod(0o600)
    sha = subprocess.run(
        ["openssl", "dgst", "-sha256", "-r", str(pem)],
        check=True, capture_output=True, text=True,
    ).stdout.split()[0]
    return {"bin": bin_dir, "record": record, "pem": pem, "pem_sha": sha, "tmp": tmp_path}


def run_kit(kit, *extra: str, modes: dict | None = None, pem: str | None = None,
            base_args: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"{kit['bin']}:{env['PATH']}"
    env["STUB_RECORD_DIR"] = str(kit["record"])
    env["STUB_PEM_SHA"] = kit["pem_sha"]
    env["TMPDIR"] = str(kit["tmp"])
    env.update(modes or {})
    args = [str(SCRIPT)]
    if base_args:
        args += [
            "--pem", pem if pem is not None else str(kit["pem"]),
            "--app-id", "12345",
            "--budget-email", "po@example.com",
            "--owner", "yomote", "--repo", "mind-inbox",
            "--yes",
        ]
    args += list(extra)
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=120)


def recorded_calls(kit, tool: str) -> list[list[str]]:
    calls = []
    for f in sorted(kit["record"].glob(f"{tool}.*.argv")):
        calls.append(f.read_text().splitlines())
    return calls


def calls_matching(kit, tool: str, *needles: str) -> list[list[str]]:
    """needle は argv の**トークン完全一致**で照合する。部分一致にすると、一時パスに
    テスト名 (例: ...refuses_apply0...) が入っただけで誤マッチする。"""
    return [c for c in recorded_calls(kit, tool) if all(n in c for n in needles)]


def assert_no_credential_leak(kit, result: subprocess.CompletedProcess) -> None:
    """pem 本文が argv / env / 出力 / 一時領域のどこにも無く、token が argv / 出力に無いこと。

    一時領域 (TMPDIR) の走査は sec P3-2 — スクリプトは TMPDIR に作業ディレクトリを
    作るので、「pem を作業ディレクトリへ cp する」変異は記録と出力だけの検査では
    緑のまま通る (実測)。source の pem ファイル自身だけは除外する。
    """
    # pem の判定材料は base64 本文の行 (ヘッダ行は一般的すぎて検出力が無い)
    pem_lines = [l for l in kit["pem"].read_text().splitlines() if "-----" not in l]
    assert pem_lines, "捨て鍵の生成が壊れている (テスト自身の前提エラー)"
    haystacks = {"stdout": result.stdout, "stderr": result.stderr}
    for f in kit["tmp"].rglob("*"):
        if not f.is_file():
            continue
        # テスト自身の fixture (source の pem / スタブのソース) は走査対象外。
        # スクリプトが**書いた**ものだけを見る。
        if f == kit["pem"] or kit["bin"] in f.parents:
            continue
        haystacks[str(f.relative_to(kit["tmp"]))] = f.read_text(errors="replace")
    for name, text in haystacks.items():
        for line in pem_lines:
            assert line not in text, f"pem 本文が {name} に漏れている"
    # installation token: terraform の子プロセス env に載るのは設計どおりなので
    # env 記録は除外し、argv と画面出力・一時領域に出ないことを見る
    for name, text in haystacks.items():
        if name.endswith(".env"):
            continue
        assert "ghs_STUBTOKEN_SECRET" not in text, f"installation token が {name} に漏れている"
        # secret show の応答は実物と同じく value 欄を持つ。--query の絞りを外すと
        # ここが丸ごと流れる — その目印 (実 pem の代わり) が出てこないこと。
        assert "STUB_SECRET_VALUE_PEM" not in text, \
            f"シークレットの value 欄が {name} に流れている (--query の絞りが外れた?)"


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------

def test_happy_path_plan_only(kit):
    """既定は plan まで。apply は呼ばれず、pem 削除の促しが出て、資格情報が漏れない。"""
    r = run_kit(kit)
    assert r.returncode == 0, r.stderr
    assert calls_matching(kit, "az", "keyvault", "secret", "set"), "pem が格納されていない"
    assert calls_matching(kit, "terraform", "plan"), "terraform plan が呼ばれていない"
    assert not calls_matching(kit, "terraform", "apply"), "既定で apply が走ってはいけない"
    assert "削除" in r.stdout and "rm " in r.stdout, "ローカル pem の削除を促していない"
    assert_no_credential_leak(kit, r)


def test_happy_path_tf_apply(kit):
    """--tf-apply かつ import-only の plan なら apply まで到達する。"""
    r = run_kit(kit, "--tf-apply")
    assert r.returncode == 0, r.stderr
    assert calls_matching(kit, "terraform", "apply", "tfplan"), "apply が呼ばれていない"
    assert_no_credential_leak(kit, r)


def test_secret_set_receives_path_not_value(kit):
    """pem は --file (パス) で渡り、--value (argv 直渡し) を使わない。"""
    r = run_kit(kit)
    assert r.returncode == 0, r.stderr
    (call,) = calls_matching(kit, "az", "keyvault", "secret", "set")
    assert "--file" in call
    assert "--value" not in call


def test_idempotent_rerun_skips_secret_set(kit):
    """同じ pem での再実行は格納をスキップし (冪等)、それでも後続へ進む。"""
    r = run_kit(kit, modes={"STUB_AZ_SECRET_SHOW": "match"})
    assert r.returncode == 0, r.stderr
    assert not calls_matching(kit, "az", "keyvault", "secret", "set"), "sha 一致なら格納しない"
    assert calls_matching(kit, "terraform", "plan"), "スキップ後も plan へ進む"


def test_changed_pem_stores_new_version(kit):
    """内容が変わった pem は新バージョンとして格納する (上書き事故ではない)。"""
    r = run_kit(kit, modes={"STUB_AZ_SECRET_SHOW": "different"})
    assert r.returncode == 0, r.stderr
    assert calls_matching(kit, "az", "keyvault", "secret", "set")
    assert "新しいバージョン" in r.stdout


# ---------------------------------------------------------------------------
# 前提確認の fail-closed
# ---------------------------------------------------------------------------

def test_missing_pem_stops_before_azure(kit):
    r = run_kit(kit, pem=str(kit["tmp"] / "no-such.pem"))
    assert r.returncode != 0
    assert not calls_matching(kit, "az", "group", "create"), "前提確認前に Azure を触ってはいけない"


def test_garbage_pem_stops_before_azure(kit):
    bad = kit["tmp"] / "garbage.pem"
    bad.write_text("これは鍵ではない\n")
    r = run_kit(kit, pem=str(bad))
    assert r.returncode != 0
    assert not calls_matching(kit, "az", "group", "create")


def test_az_not_logged_in_stops_before_mutation(kit):
    r = run_kit(kit, modes={"STUB_AZ_ACCOUNT": "fail"})
    assert r.returncode != 0
    assert not calls_matching(kit, "az", "group", "create")
    assert not calls_matching(kit, "az", "keyvault", "secret", "set")


# ---------------------------------------------------------------------------
# Azure 段の fail-closed — 落ちたら台帳を出し、後続 (KV / terraform) へ進まない
# ---------------------------------------------------------------------------

def test_deploy_failure_is_fail_closed(kit):
    r = run_kit(kit, modes={"STUB_AZ_DEPLOY": "fail"})
    assert r.returncode != 0
    assert "失敗" in r.stderr and "未着手" in r.stderr, "何が済んで何が済んでいないかの台帳が出ていない"
    assert not calls_matching(kit, "az", "keyvault", "secret", "set"), "apply 失敗後に格納へ進んではいけない"
    assert not calls_matching(kit, "terraform", "plan")
    # 格納前の失敗で pem 削除を促してはいけない (促すと再実行できる鍵を失う)
    assert "格納は完了しています" not in r.stderr


def test_untagged_resource_fails_verification(kit):
    """層タグの無い資源 = 撤収ガードから見えない資源。黙って通すと撤収で消える。"""
    r = run_kit(kit, modes={"STUB_AZ_RESOURCE": "untagged"})
    assert r.returncode != 0
    assert "stray-storage" in r.stderr
    assert not calls_matching(kit, "az", "keyvault", "secret", "set")


def test_exportable_key_fails_verification(kit):
    """エクスポート可能な鍵で止まること。

    無いと何が静かに通るか (#499 発見 2):
        **鍵の検証が「常に空を見ている」状態に戻る。** `exportable` は応答の
        `attributes` の下にあり、`key.exportable` は存在しないパス — az は空文字を
        返すだけで非ゼロにならない。スタブが実応答の形を写している (query を実際に
        評価する) いま、query を `key.exportable` に戻すとこのテストが赤くなる
        (エクスポート可能な鍵を見逃して完走してしまうため)。
    """
    r = run_kit(kit, modes={"STUB_AZ_KEY": "exportable"})
    assert r.returncode != 0
    assert "exportable" in r.stderr
    # KV 格納より前に止まる (設計が崩れた Vault へ pem を入れない)
    assert not calls_matching(kit, "az", "keyvault", "secret", "set")


def test_単体_鍵の検証は_attributes_exportable_を問い合わせる(kit):
    """問い合わせ先のパスそのものを固定する。

    無いと何が静かに通るか: 上のテストは「エクスポート可能なら止まる」を見るが、
    正常系 (空が返る) は誤った query でも同じく空なので素通りする。パスを直接
    押さえておかないと、`key.exportable` への差し戻しが**正常系では**気づけない。
    """
    r = run_kit(kit)
    assert r.returncode == 0, r.stderr
    (call,) = calls_matching(kit, "az", "keyvault", "key", "show")
    assert "attributes.exportable" in call, \
        f"exportable は attributes の下 (key.exportable は常に空を返す): {call}"


def test_単体_az_が_tsv_で_True_と返してもエクスポート可能を見逃さない(kit):
    """az の tsv が bool を `True` と大文字で返す版でも落ちること。

    無いと何が静かに通るか: 判定をシェルの `[ "$x" != "true" ]` 1 行に戻すと、
    大文字で返す az では**エクスポート可能な鍵が素通りする**。ローカルから実 az を
    叩けない以上、どちらの表記でも同じ判定になることをここで固定するしかない。
    """
    r = run_kit(kit, modes={"STUB_AZ_KEY": "exportable", "STUB_AZ_TSV_BOOL": "title"})
    assert r.returncode != 0
    assert "exportable" in r.stderr
    assert not calls_matching(kit, "az", "keyvault", "secret", "set")


# ---------------------------------------------------------------------------
# Key Vault データプレーン権限の事前確認 (#499 発見 1)
# ---------------------------------------------------------------------------

def test_単体_データプレーン権限が無いと鍵の検証より前に止まり付与コマンドが出る(kit):
    """RBAC 不足は**データプレーンを使う前に**止まり、ロールとコマンドを名指しする。

    無いと何が静かに通るか:
        **権限不足が「鍵が壊れている」「az が変」に見える。** Key Vault は RBAC 方式で
        control-plane のロール (Contributor / Owner) に data-plane は含まれない。
        事前確認が無いと、最初に data-plane を触る検証 6b が `ForbiddenByRbac` で
        落ち、PO は「鍵の作られ方がおかしい」と読む (実測 2026-08-17)。しかも
        直し方 (どのロールをどのスコープに) がどこにも出ない。
    """
    r = run_kit(kit, modes={"STUB_KV_DATAPLANE": "forbidden"})
    assert r.returncode != 0
    # 何をどう直すかが出る
    assert "Key Vault Reader" in r.stderr and "Key Vault Secrets Officer" in r.stderr
    assert "az role assignment create" in r.stderr
    assert "/providers/Microsoft.KeyVault/vaults/kv-dev-mindbox" in r.stderr, \
        "付与先スコープ (vault の resource id) を名指ししていない"
    # data-plane を実際に使う手前で止まっている
    assert not calls_matching(kit, "az", "keyvault", "key", "show"), \
        "権限が無いまま鍵の検証へ進んではいけない (原因が鍵の側に見える)"
    assert not calls_matching(kit, "az", "keyvault", "secret", "set")
    assert not calls_matching(kit, "terraform")
    assert "格納は完了しています" not in r.stderr


def test_単体_鍵だけ拒否されたときは鍵側のロールだけを名指しする(kit):
    """通っている方のロールまで付けさせない (確認の副作用で過剰権限を勧めない)。"""
    r = run_kit(kit, modes={"STUB_KV_DATAPLANE": "keys-forbidden"})
    assert r.returncode != 0
    assert "Key Vault Reader" in r.stderr
    assert "Key Vault Secrets Officer" not in r.stderr, \
        "通っている secrets 側のロールまで付与させている"


def test_単体_権限確認そのものが失敗したら未検証として止まる(kit):
    """権限以外の失敗 (Vault 名の誤り / 到達不能) を「ロールを付けろ」と言わない。

    無いと何が静かに通るか: 非ゼロを一律「権限不足」と読むと、PO はロールを付けても
    直らない指示を踏み続ける。逆に握り潰せば**権限が無いまま「確認 OK」**になる。
    """
    r = run_kit(kit, modes={"STUB_KV_DATAPLANE": "error"})
    assert r.returncode != 0
    assert "未検証" in r.stderr, "確かめられなかったことを成功と区別していない"
    assert "az role assignment create" not in r.stderr, \
        "権限の問題ではないのにロール付与を案内している"
    assert "VaultNotFound" in r.stderr, "az の生のエラーを隠している (原因に辿り着けない)"
    assert not calls_matching(kit, "az", "keyvault", "key", "show")


def test_単体_権限が揃っていれば事前確認は素通りする(kit):
    """対照実験: 上の 3 本が「常に止まるだけの検査」になっていないこと。"""
    r = run_kit(kit)
    assert r.returncode == 0, r.stderr
    assert calls_matching(kit, "az", "keyvault", "key", "list"), "鍵側のプローブが走っていない"
    assert calls_matching(kit, "az", "keyvault", "secret", "list"), "シークレット側のプローブが走っていない"
    assert calls_matching(kit, "az", "keyvault", "key", "show"), "事前確認の後に検証へ進んでいない"


def test_単体_事前確認はシークレットの書き込みを未検証として明示する(kit):
    """確かめた範囲だけを言う (#508 Codex P2)。

    無いと何が静かに通るか:
        **未検証の権限が台帳の [済] に化ける。** プローブは読み取りだけなので、
        `Key Vault Reader` しか持たない実行者でも `key list` / `secret list` は両方
        成功する。それを「pem の格納の権限確認 OK」と表示すると、**書き込みは一度も
        確かめていないのに成功扱い**になり、失敗時の台帳も嘘をつく
        (root CLAUDE.md「取れなかったものを『異常なし』と書かない」)。
    """
    r = run_kit(kit)
    assert r.returncode == 0, r.stderr
    assert "未検証" in r.stdout, "書き込み権限が未検証であることを言っていない"
    # 台帳のステップ名が「pem の格納まで確認した」と読めないこと
    ledger_line = next(
        (ln for ln in r.stdout.splitlines() if "データプレーン権限の確認" in ln and "[済]" in ln),
        None,
    )
    assert ledger_line is not None, "台帳に事前確認の行が出ていない"
    assert "pem の格納" not in ledger_line, \
        f"確かめていない pem 格納まで [済] に見える台帳になっている: {ledger_line}"


def test_missing_budget_fails_verification(kit):
    """予算の不在は「どの予算にも載らない RG」なので成功扱いにしない。"""
    r = run_kit(kit, modes={"STUB_AZ_BUDGET": "absent"})
    assert r.returncode != 0
    assert "予算" in r.stderr or "budget" in r.stderr.lower()
    assert not calls_matching(kit, "az", "keyvault", "secret", "set")


def test_kv_forbidden_prints_role_guidance(kit):
    """RBAC 不足は「何のロールをどこに付けるか」まで言って止まる。"""
    r = run_kit(kit, modes={"STUB_AZ_SECRET_SET": "forbidden"})
    assert r.returncode != 0
    assert "Key Vault Secrets Officer" in r.stderr
    assert not calls_matching(kit, "terraform"), "格納失敗後に terraform へ進んではいけない"
    # 格納に失敗している = 未格納なので、pem 削除を促してはいけない
    assert "格納は完了しています" not in r.stderr


# ---------------------------------------------------------------------------
# GitHub App 認証の fail-closed
# ---------------------------------------------------------------------------

def test_app_id_mismatch_is_detected_before_secret_set(kit):
    """違う App の pem を掴んだ取り違えは、**KV 格納より前に**止まる (Codex P1)。
    逆順だと、別 App の有効な pem で KV の最新バージョンを潰してから気づく。"""
    r = run_kit(kit, modes={"STUB_GH_APP": "mismatch"})
    assert r.returncode != 0
    assert "一致しません" in r.stderr
    assert not calls_matching(kit, "az", "keyvault", "secret", "set"), \
        "App 検証前に格納してはいけない (既存資格情報の破壊経路)"
    assert not calls_matching(kit, "terraform")


def test_not_installed_gives_install_url(kit):
    r = run_kit(kit, modes={"STUB_GH_INSTALLS": "none"})
    assert r.returncode != 0
    assert "installations/new" in r.stderr
    assert not calls_matching(kit, "az", "keyvault", "secret", "set"), \
        "App 検証前に格納してはいけない (Codex P1)"
    assert not calls_matching(kit, "terraform")


def test_token_without_admin_write_is_rejected(kit):
    r = run_kit(kit, modes={"STUB_GH_TOKEN": "noadmin"})
    assert r.returncode != 0
    assert "administration" in r.stderr
    assert not calls_matching(kit, "az", "keyvault", "secret", "set"), \
        "App 検証前に格納してはいけない (Codex P1)"
    assert not calls_matching(kit, "terraform")


def test_単体_余分な権限を持つ_App_は_KV_格納の前に止まる(kit):
    """余分な権限を持つ App は **KV 格納より前に**止まる (#498 Codex P2)。

    無いと何が静かに通るか:
        **過剰権限の App が「動くので」そのまま定着する。** App は手動フォームで
        作る (#497) ため、権限が 2 つに絞れているかを機械が見る場所はこの
        installation token 応答しかない。「administration が write か」だけの
        下限チェックだと Contents / Actions を余分に付けた App でも満たすので、
        その pem が Key Vault に入り、以後の plan/apply が過剰権限で回り続ける。
    """
    r = run_kit(kit, modes={"STUB_GH_TOKEN": "extra"})
    assert r.returncode != 0
    assert "余分" in r.stderr, "何が余分なのかを言わずに止まっている"
    # 何が余分かが名指しで出る (「権限が違います」だけでは PO が直せない)
    assert "contents=read" in r.stderr and "actions=write" in r.stderr
    assert not calls_matching(kit, "az", "keyvault", "secret", "set"), \
        "過剰権限 App の pem を Key Vault へ入れてはいけない"
    assert not calls_matching(kit, "terraform")


def test_単体_権限が不足している_App_も止まる(kit):
    """不足側も落とす (完全一致であって「余分が無ければ OK」ではない)。"""
    r = run_kit(kit, modes={"STUB_GH_TOKEN": "nometa"})
    assert r.returncode != 0
    assert "不足" in r.stderr and "metadata=read" in r.stderr
    assert not calls_matching(kit, "az", "keyvault", "secret", "set")
    assert not calls_matching(kit, "terraform")


def test_単体_想定どおりの_2_権限なら_KV_格納まで到達する(kit):
    """対照実験: 想定どおりの 2 権限ちょうどなら通り、KV 格納まで到達する。
    上の 3 つが「常に落ちるだけの検査」になっていないことをここで固定する。"""
    r = run_kit(kit)
    assert r.returncode == 0, r.stderr
    assert "完全一致" in r.stdout, "権限集合を完全一致で検証した旨が出ていない"
    assert calls_matching(kit, "az", "keyvault", "secret", "set")


# ---------------------------------------------------------------------------
# 権限集合の照合そのもの — 判定を純粋関数として直接叩く (#498 Codex P2 3 回目)
#
# 上のスタブ経由テストは「シェルが判定を呼び、結果どおりに止まる / 進む」という
# **配線**を見る。ここで見るのは**判定の中身**で、Azure も curl も openssl も要らない。
#
# 無いと何が静かに通るか:
#   分類 (余分 / 値ちがい / 不足) を 1 種類でも見落とす変更が、スクリプト全体を
#   スタブで流す経路でしか検出できなくなる。スタブは curl の応答パターンを足した
#   ぶんしか分岐を持てないので、**用意し忘れた組み合わせ (値ちがいと不足の同時発生
#   など) は誰も見ない**まま「下限チェックへの退行」に戻れてしまう。
# ---------------------------------------------------------------------------

def test_単体_余分な権限は余分として名指しされる():
    got = {"administration": "write", "metadata": "read",
           "contents": "read", "actions": "write"}
    report = compare_permissions(EXPECTED, got)
    assert not report.matches
    assert report.extra == ("actions=write", "contents=read")
    assert report.wrong == () and report.missing == ()


def test_単体_レベルが違う権限は値ちがいとして名指しされる():
    """`administration: read` は「余分」でも「不足」でもない。**別欄**にしないと
    PO は権限を外そうとしてしまい、直し方を間違える。"""
    report = compare_permissions(EXPECTED, {"administration": "read", "metadata": "read"})
    assert not report.matches
    assert report.wrong == ("administration=read (期待 write)",)
    assert report.extra == () and report.missing == ()


def test_単体_欠けている権限は不足として名指しされる():
    report = compare_permissions(EXPECTED, {"administration": "write"})
    assert not report.matches
    assert report.missing == ("metadata=read",)
    assert report.extra == () and report.wrong == ()


def test_単体_想定と完全一致なら_3_欄すべてが空になる():
    """対照実験。上の 3 本が「常に何か言う検査」になっていないことをここで固定する。
    これが無いと、`extra = 全部` のような雑な実装でも 3 本とも緑になる。"""
    report = compare_permissions(EXPECTED, dict(EXPECTED))
    assert report.matches
    assert report == PermissionReport(extra=(), wrong=(), missing=())
    assert report.render() == "||", "一致時は 3 欄とも空 (シェルは空文字で分岐する)"


def test_単体_余分と値ちがいと不足は同時に報告される():
    """3 つが同時に起きる App でも、最初の 1 つで打ち切らずに全部出す
    (打ち切ると PO は直して再実行するたびに 1 つずつしか進めない)。"""
    report = compare_permissions(
        EXPECTED, {"administration": "read", "contents": "write"}
    )
    assert report.extra == ("contents=write",)
    assert report.wrong == ("administration=read (期待 write)",)
    assert report.missing == ("metadata=read",)
    # シェル (bootstrap.sh の ${perm_report%%|*} 系) が読む 1 行の書式そのもの
    assert report.render() == "contents=write|administration=read (期待 write)|metadata=read"


def test_単体_CLI_は不一致でも終了コード_0_で_3_欄を返す(tmp_path: Path):
    """シェルは `set -euo pipefail` 下の `$(...)` で受ける。ここで非ゼロを返すと、
    「何が余分か」を出す前に errexit で落ちて PO 向けの文面が消える。"""
    resp = tmp_path / "resp.json"
    resp.write_text(json.dumps(
        {"token": "ghs_x", "permissions": {"administration": "write", "contents": "read"}}
    ))
    r = subprocess.run(
        [sys.executable, str(CHECK_PERMISSIONS), str(resp),
         "administration=write", "metadata=read"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "contents=read||metadata=read"


def test_単体_シェルの想定権限集合が単体テストの期待値と一致する():
    """`EXPECTED_PERMISSIONS` を根拠なく広げる変更をここで止める。

    無いと何が静かに通るか: 判定関数の単体テストは EXPECTED を直書きしているので、
    シェル側に `contents=read` を足しても単体は全部緑のまま通る。"""
    m = re.search(r"^EXPECTED_PERMISSIONS=\(([^)]*)\)", SCRIPT.read_text(), re.M)
    assert m, "bootstrap.sh に EXPECTED_PERMISSIONS の宣言が無い (表記が変わった?)"
    pairs = dict(tok.strip('"').split("=", 1) for tok in m.group(1).split())
    assert pairs == EXPECTED, \
        f"bootstrap.sh の想定権限 {pairs} が単体テストの期待値 {EXPECTED} とずれている"


def test_単体_判定は_bootstrap_sh_の中に埋め戻されていない():
    """判定を heredoc で書き戻す退行を静的に止める (cicd/CLAUDE.md)。

    無いと何が静かに通るか: check_permissions.py を残したまま、シェル側に
    `python3 - <<PY` で判定を書き戻しても、振る舞いテストは全部緑のまま通る
    (= 純粋関数のテストが実際には何も守っていない状態に戻る)。"""
    text = SCRIPT.read_text()
    assert "check_permissions.py" in text, "bootstrap.sh が判定モジュールを呼んでいない"
    assert "<<'PY'" not in text and "<<PY" not in text, \
        "bootstrap.sh に Python の heredoc を戻さない (判定は check_permissions.py へ)"


# ---------------------------------------------------------------------------
# Key Vault の判定そのもの — 判定を純粋関数として直接叩く (#499)
#
# 上のスタブ経由テストは「シェルが判定を呼び、結果どおりに止まる / 進む」という
# **配線**を見る。ここで見るのは**判定の中身**で、az も Vault も要らない。
#
# 無いと何が静かに通るか:
#   スタブは用意した分岐しか持てないので、**用意し忘れた組み合わせ** (片方だけ
#   forbidden、rc は非ゼロだが権限とは別の失敗、bool の表記ゆれ) は誰も見ないまま
#   「非ゼロ = 権限不足」「空 = false」の雑な判定に戻れてしまう。
# ---------------------------------------------------------------------------

def test_単体_exportable_は空でも_false_でも正常とみなす():
    """**空が正常**。エクスポート不可の鍵は attributes に exportable を持たない。

    ここを「`false` と一致したら OK」に戻すと、正常な鍵で必ず止まる (#499 発見 2 の実害)。
    """
    assert key_export_verdict("") == "ok"
    assert key_export_verdict("\n") == "ok"
    assert key_export_verdict("false") == "ok"
    assert key_export_verdict("False") == "ok"  # az の tsv が大文字で返す版
    assert key_export_verdict("null") == "ok"


def test_単体_exportable_が_true_なら表記に関わらず落とす():
    assert key_export_verdict("true") == "exportable"
    assert key_export_verdict("True") == "exportable"
    assert key_export_verdict(" TRUE\n") == "exportable"


def test_単体_読めない_exportable_は_ok_にしない():
    """判定できなかったものを「異常なし」にしない (root CLAUDE.md)。"""
    assert key_export_verdict("yes") == "unknown"
    assert key_export_verdict("{}") == "unknown"


def test_単体_プローブの分類は権限エラーとそれ以外を分ける():
    forbidden = (
        "ERROR: (ForbiddenByRbac) Caller is not authorized to perform action on resource.\n"
        "Assignment: (not found)\n"
    )
    assert classify_probe(1, forbidden) == "forbidden"
    assert classify_probe(1, "The user does not have keys list permission on key vault") == "forbidden"
    assert classify_probe(1, "ERROR: (VaultNotFound) Vault not found") == "error"
    # rc が 0 なら stderr に何が出ていても ok (az は警告を stderr に書く)
    assert classify_probe(0, "WARNING: the default value will change") == "ok"


def test_単体_拒否されたプローブに対応するロールだけを返す():
    report = data_plane_verdict({
        "keys": (1, "ForbiddenByRbac: Assignment: (not found)"),
        "secrets": (0, ""),
    })
    assert report.status == "forbidden"
    assert report.roles == ("Key Vault Reader",)
    assert "検証 6b" in report.detail


def test_単体_両方拒否なら_2_つのロールを返す():
    report = data_plane_verdict({
        "keys": (1, "ForbiddenByRbac"),
        "secrets": (1, "ForbiddenByRbac"),
    })
    assert report.status == "forbidden"
    assert report.roles == ("Key Vault Reader", "Key Vault Secrets Officer")


def test_単体_権限以外の失敗は_forbidden_より優先して_error_になる():
    """片方が権限不足でも、もう片方が「確かめられなかった」なら error。

    無いと何が静かに通るか: 先に forbidden を見て返すと、Vault 名の誤りやネットワーク断が
    「ロールを付ければ直る」と案内され、PO は付与しても直らない指示を踏み続ける。
    """
    report = data_plane_verdict({
        "keys": (1, "ForbiddenByRbac"),
        "secrets": (1, "(VaultNotFound) Vault not found: kv-typo-mindbox"),
    })
    assert report.status == "error"
    assert report.roles == ()
    assert "VaultNotFound" in report.detail


def test_単体_両方通れば_3_欄は_ok_と空になる():
    """対照実験。上の 3 本が「常に何か言う検査」になっていないことをここで固定する。"""
    report = data_plane_verdict({"keys": (0, ""), "secrets": (0, "")})
    assert report == DataPlaneReport(status="ok", roles=(), detail="")
    assert report.render() == "ok||", "一致時は 2 欄とも空 (シェルは空文字で分岐する)"


def test_単体_未知のプローブ名は落とす():
    """綴り間違いを「該当なし = OK」にしない。"""
    with pytest.raises(KeyError):
        data_plane_verdict({"keyz": (1, "ForbiddenByRbac")})


def test_単体_detail_は改行と区切り文字を含まない():
    """シェルは 1 行として読む。改行や `|` が混ざると欄の切り出しが壊れる。"""
    report = data_plane_verdict({"keys": (1, "line one\nline|two\nline three")})
    assert "\n" not in report.detail and FIELD_SEPARATOR not in report.detail
    assert report.render().count(FIELD_SEPARATOR) == 2


def test_単体_CLI_は判定結果で終了コードを変えない(tmp_path: Path):
    """シェルは `set -euo pipefail` 下の `$(...)` で受ける。ここで非ゼロを返すと
    PO 向けの付与手順を出す前に errexit で落ちる (check_permissions.py と同じ約束)。"""
    err = tmp_path / "keys.err"
    err.write_text("ERROR: (ForbiddenByRbac) ...\nAssignment: (not found)\n")
    ok = tmp_path / "secrets.err"
    ok.write_text("")
    r = subprocess.run(
        [sys.executable, str(CHECK_KEY_VAULT), "data-plane",
         "keys", "1", str(err), "secrets", "0", str(ok)],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().startswith("forbidden|Key Vault Reader|")

    r2 = subprocess.run(
        [sys.executable, str(CHECK_KEY_VAULT), "key-export", "True"],
        capture_output=True, text=True, timeout=60,
    )
    assert r2.returncode == 0, r2.stderr
    assert r2.stdout.strip() == "exportable"


def test_単体_判定は_bootstrap_sh_の中に埋め戻されていない_key_vault():
    """Key Vault 側の判定も heredoc で書き戻す退行を静的に止める (cicd/CLAUDE.md)。"""
    text = SCRIPT.read_text()
    assert "check_key_vault.py" in text, "bootstrap.sh が Key Vault の判定モジュールを呼んでいない"


# ---------------------------------------------------------------------------
# Terraform 段 — #387 の門
# ---------------------------------------------------------------------------

def test_plan_drift_refuses_apply(kit):
    """add/change/destroy が 1 つでもあれば --tf-apply でも apply しない (#387)。"""
    r = run_kit(kit, "--tf-apply", modes={"STUB_TF_PLAN": "drift"})
    assert r.returncode != 0
    assert "#387" in r.stderr
    assert not calls_matching(kit, "terraform", "apply", "tfplan")
    # KV 格納後の失敗なので、pem 削除の案内がエラー側にも出る (Codex P2)
    assert "格納は完了しています" in r.stderr and "rm " in r.stderr, \
        "格納済みの失敗経路で pem 削除手順を出していない"


def test_plan_without_summary_is_not_success(kit):
    """集計行が読めない plan は「差分不明」— 「差分なし」と読み替えない。"""
    r = run_kit(kit, "--tf-apply", modes={"STUB_TF_PLAN": "nosummary"})
    assert r.returncode != 0
    assert not calls_matching(kit, "terraform", "apply", "tfplan")


def test_plan_failure_stops(kit):
    r = run_kit(kit, modes={"STUB_TF_PLAN": "fail"})
    assert r.returncode != 0
    assert "plan が失敗" in r.stderr


# ---------------------------------------------------------------------------
# 静的な安全装置
# ---------------------------------------------------------------------------

def test_no_set_x_in_script():
    """`set -x` は変数展開 (token を含む) を stderr に吐く。漏えい検査 (実行系) は
    pem しか見ていないので、トレース有効化は静的に禁止して固定する。"""
    text = SCRIPT.read_text()
    assert re.search(r"^\s*set\s+[-+]?\w*x", text, re.M) is None, \
        "bootstrap.sh に set -x を足してはいけない (冒頭コメント参照)"
    # 同義の別表記も塞ぐ (sec P3-3): set -o xtrace / set +o xtrace
    assert re.search(r"^\s*set\s+[-+]o\s+xtrace\b", text, re.M) is None, \
        "bootstrap.sh に set -o xtrace を足してはいけない (set -x と同義)"


def _table_rows_after_heading(md: str, heading_needle: str) -> list[list[str]]:
    """見出し `heading_needle` を含む行の**直後に最初に現れる** markdown テーブルの
    データ行を、セルのリストとして返す。見つからなければ落とす (取れなかったものを
    「空でした」として返さない)。"""
    lines = md.splitlines()
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith("#") and heading_needle in ln),
        None,
    )
    assert start is not None, f"README に見出し '{heading_needle}' が無い"
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= set("-: ") and c for c in cells):
                continue  # 区切り行
            rows.append(cells)
        elif rows:
            break  # テーブルが終わった
        elif stripped.startswith("#"):
            pytest.fail(f"'{heading_needle}' の直後にテーブルが無い (次の見出しに到達)")
    assert rows, f"'{heading_needle}' の下にテーブルの行が無い"
    return rows[1:]  # ヘッダ行を落とす


# フォーム表記 (PO が画面で選ぶ文言) ↔ API 表記 (根拠表・installation token の permissions)
FORM_LEVEL_TO_API = {"Read and write": "write", "Read-only": "read"}


def test_単体_README_の権限表_2_つが同じ権限集合を指す():
    """README の 2 つの表 —「フォームに入れる値」(PO が画面に写す正典) と
    「入れた権限」(1 行ずつの根拠) — が同じ権限集合を指していること。

    無いと何が静かに通るか:
        **根拠の無い権限を持つ App が作られる。** App の作成は手動フォームなので、
        権限集合を機械が読む場所は README のこの 2 表しか無い。bootstrap.sh は
        installation token の `administration` が write **であること**しか見ておらず
        (下限の確認)、余分な権限が付いていても素通りする。よって片方の表にだけ
        権限行を足す / 値をずらす変更は、テストも実行も緑のまま通ってしまう。
    """
    md = README.read_text()

    form = {}
    for cells in _table_rows_after_heading(md, "Repository permissions"):
        name, level = cells[0].strip("`* "), cells[1].strip("`* ")
        assert level in FORM_LEVEL_TO_API, \
            f"'{name}' の設定値 '{level}' は GitHub のフォームに無い表記 (Read and write / Read-only)"
        form[name.lower()] = FORM_LEVEL_TO_API[level]

    granted = set()
    for cells in _table_rows_after_heading(md, "入れた権限"):
        cell = cells[0].strip()
        if cell in ("〃", "同上", ""):
            continue  # 直前の権限に対する追加の根拠行
        m = re.fullmatch(r"`([a-z_]+):\s*(read|write)`", cell)
        assert m, f"「入れた権限」表の権限セルが `name: level` 形式でない: {cell!r}"
        granted.add((m.group(1), m.group(2)))

    assert form == {"administration": "write", "metadata": "read"}, \
        f"フォームに入れる権限は根拠を書いた 2 つだけ: {form}"
    assert granted == set(form.items()), \
        f"根拠表 {granted} とフォームの表 {set(form.items())} がずれている (足すなら両方同じ PR で)"


def test_単体_却下した権限がフォームの表に入っていない():
    """「入れなかった権限」に挙げた権限が、フォームの表に入り込んでいないこと
    (= 却下の記録を残したまま黙って付与する、を落とす)。"""
    md = README.read_text()
    granted = {
        cells[0].strip("`* ").lower()
        for cells in _table_rows_after_heading(md, "Repository permissions")
    }
    rejected = set()
    for cells in _table_rows_after_heading(md, "入れなかった権限"):
        rejected |= {n.lower() for n in re.findall(r"`([a-z_]+)`", cells[0])}
    assert rejected, "「入れなかった権限」表から権限名を 1 つも読めていない (表記が変わった?)"
    assert not (rejected & granted), \
        f"却下したはずの権限がフォームの表に入っている: {sorted(rejected & granted)}"
