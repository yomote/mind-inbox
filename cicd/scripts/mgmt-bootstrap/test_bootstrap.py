"""bootstrap.sh の分岐を、az / terraform / curl をスタブして振る舞いで固定する。

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
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

KIT_DIR = Path(__file__).resolve().parent
SCRIPT = KIT_DIR / "bootstrap.sh"
README = KIT_DIR / "README.md"

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
  *"keyvault key show"*)
    if [ "${STUB_AZ_KEY:-ok}" = "exportable" ]; then echo "true"; else echo "false"; fi ;;
  *"rest --method get"*)
    [ "${STUB_AZ_BUDGET:-ok}" = "absent" ] && { echo "BudgetNotFound" >&2; exit 1; }
    echo "1" ;;
  *"keyvault secret show"*)
    case "${STUB_AZ_SECRET_SHOW:-absent}" in
      match) echo "${STUB_PEM_SHA:?}" ;;
      different) echo "deadbeefdeadbeef" ;;
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
    if [ "${STUB_GH_TOKEN:-ok}" = "noadmin" ]; then
      echo '{"token": "ghs_STUBTOKEN_SECRET", "permissions": {"administration": "read", "metadata": "read"}}' > "$out"
    else
      echo '{"token": "ghs_STUBTOKEN_SECRET", "permissions": {"administration": "write", "metadata": "read"}}' > "$out"
    fi ;;
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
    r = run_kit(kit, modes={"STUB_AZ_KEY": "exportable"})
    assert r.returncode != 0
    assert "exportable" in r.stderr


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


def test_readme_permission_tables_agree():
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


def test_readme_rejected_permissions_are_not_granted():
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
