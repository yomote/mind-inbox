"""[契約] `main-mgmt.bicep` の層タグと、撤収ガードが見る定数が一致しているか。

**無いと何が静かに通るか**: 層タグは「宣言側が刻む値」と「ガード側が探す値」の
2 箇所に分かれて存在する。**片方だけを変えても、どちらのテストも緑のまま通る** —
ガード側の pytest はガード側の定数を使って組み立てた入力を食わせているので、
bicep の値が別物になっても気づけない。ズレた瞬間に起きるのは
「**管理系のつもりで作ったリソースが、撤収ガードからはアプリ系に見える**」で、
Key Vault (E2E trace の復号鍵) とバックアップ Storage が黙って消える。

同じ理由で「タグの付け忘れ」も見る。`main-mgmt.bicep` にリソースを足したとき
`tags: managementLayerTags` を書き忘れると、そのリソースだけが撤収で消える。

## 2 層で見る (片方が動かない環境でも、どこまで確かめたかを黙らない)

1. **bicep ソースの照合** — どこでも走る。タグ変数の値・キーと、
   `tags:` を書いた top-level resource を、テキストとして読む
2. **コンパイル後の ARM JSON の照合** — `az bicep build` / `bicep build` がある
   ときだけ走る。`union()` や条件式で**評価後に**値が変わる経路まで捕まえる。
   `npm run test:scripts` の CI (test.yml) には bicep が無いので通常はここが
   skip される。**skip は「通った」ではない**ので、bicep のある
   `iac-validate.yml` がこのファイルを pytest で走らせ、**skip が出たら job を
   落とす** (bicep が見えていない = 未検証のまま緑、を潰すため)。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from persistent_layer_guard import LAYER_TAG_KEY, LAYER_TAG_MANAGEMENT_VALUE

BICEP = Path(__file__).resolve().parents[2] / "iac" / "main-mgmt.bicep"

# 層タグを持たせる変数名。bicep 側でこの名前を変えたらここも変える
# (変数名が変わると下の「tags: <var>」照合が空振りするので、テストが赤くなって気づける)。
TAG_VAR = "managementLayerTags"

# **ARM のリソース型のうち、タグを持てないもの。** ここに入れたものはタグ検査から
# 外れるので、足すときは「本当にタグを持てないのか」を確かめること
# (持てるのに入れると、その型が黙って撤収で消える経路ができる)。
UNTAGGABLE_TYPES = {
    # ロール割り当ては ARM の拡張リソースで tags を受け付けない。
    "microsoft.authorization/roleassignments",
    # Consumption の budget は tags を受け付けない。
    "microsoft.consumption/budgets",
}


def _bicep_source() -> str:
    return BICEP.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. bicep ソースの照合 (どこでも走る)
# ---------------------------------------------------------------------------


def _tag_object_literal(source: str) -> str:
    """`var managementLayerTags = { ... }` の中身を返す。"""
    match = re.search(
        rf"var\s+{TAG_VAR}\s*=\s*\{{(.*?)\n\}}", source, flags=re.DOTALL
    )
    assert match is not None, (
        f"{BICEP.name} に `var {TAG_VAR} = {{ ... }}` が見つかりません。"
        "変数名を変えたなら、このテストの TAG_VAR も同じ PR で直してください"
    )
    return match.group(1)


def test_bicep_layer_tag_value_matches_guard_constant() -> None:
    """bicep が刻む層タグの値が、ガードの探す値と同じか。

    無いと: bicep 側だけ旧値 (`persistent`) に戻しても全テストが緑のままで、
    管理系リソースがアプリ系として撤収される。
    """
    literal = _tag_object_literal(_bicep_source())
    pairs = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", literal))

    keys = {k.lower(): v for k, v in pairs.items()}
    assert LAYER_TAG_KEY in keys, (
        f"層タグのキーが {BICEP.name} に見つかりません "
        f"(ガードが探すのは {LAYER_TAG_KEY}、宣言側にあるのは {list(pairs)})"
    )
    assert keys[LAYER_TAG_KEY] == LAYER_TAG_MANAGEMENT_VALUE, (
        f"層タグの値がズレています: bicep={keys[LAYER_TAG_KEY]!r} / "
        f"guard={LAYER_TAG_MANAGEMENT_VALUE!r}"
    )


def _toplevel_resources_in_source(source: str) -> list[tuple[str, str, str]]:
    """`resource <symbol> '<type>@<ver>' = ...` を (symbol, type, body) で返す。

    body は次の `resource` / `output` 宣言までの塊 (tags の有無を見るのに使う)。
    """
    decls = list(
        re.finditer(
            r"^resource\s+(\w+)\s+'([^'@]+)@[^']+'", source, flags=re.MULTILINE
        )
    )
    out: list[tuple[str, str, str]] = []
    for i, m in enumerate(decls):
        end = decls[i + 1].start() if i + 1 < len(decls) else len(source)
        out.append((m.group(1), m.group(2), source[m.start() : end]))
    return out


def _is_child(resource_type: str) -> bool:
    """`Microsoft.KeyVault/vaults/keys` のような子リソースか (親だけ見れば足りる)。"""
    return resource_type.count("/") >= 2


def test_bicep_taggable_toplevel_resources_carry_the_layer_tag() -> None:
    """タグを持てる top-level resource がすべて層タグを持っているか。

    無いと: `main-mgmt.bicep` にリソースを足したとき `tags:` を書き忘れても
    誰も気づかず、そのリソースだけが撤収ガードからアプリ系に見えて消える。
    """
    source = _bicep_source()
    missing: list[str] = []
    checked = 0
    for symbol, resource_type, body in _toplevel_resources_in_source(source):
        if _is_child(resource_type) or resource_type.lower() in UNTAGGABLE_TYPES:
            continue
        checked += 1
        if not re.search(rf"^\s*tags:\s*{TAG_VAR}\s*$", body, flags=re.MULTILINE):
            missing.append(f"{symbol} ({resource_type})")

    # 検査対象が 0 件なら「全部通った」ではなく**照合が空振りしている**。
    assert checked > 0, (
        f"{BICEP.name} からタグ対象の top-level resource を 1 つも拾えませんでした。"
        "宣言の書き方が変わってこのテストが空振りしています"
    )
    assert not missing, (
        f"層タグ (`tags: {TAG_VAR}`) の無い top-level resource: {missing}。"
        "付け忘れたリソースは撤収ガードからアプリ系に見えて消えます"
    )


# ---------------------------------------------------------------------------
# 2. コンパイル後の ARM JSON の照合 (bicep があるときだけ)
# ---------------------------------------------------------------------------


def _bicep_command() -> list[str] | None:
    # 引数の形が 2 つある: 素の bicep CLI は位置引数、az 経由は --file。
    if shutil.which("bicep"):
        return ["bicep", "build", str(BICEP), "--stdout"]
    if shutil.which("az"):
        return ["az", "bicep", "build", "--file", str(BICEP), "--stdout"]
    return None


def _compile_arm() -> dict:
    cmd = _bicep_command()
    assert cmd is not None, "bicep が無い"
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"bicep build が失敗しました:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _arm_layer_tag_findings(template: dict) -> tuple[list[str], list[str], int]:
    """(値がズレているもの, タグの無いもの, 検査した件数)。"""
    variables = template.get("variables") or {}
    wrong: list[str] = []
    missing: list[str] = []
    checked = 0

    for resource in template.get("resources") or []:
        resource_type = str(resource.get("type") or "")
        if _is_child(resource_type) or resource_type.lower() in UNTAGGABLE_TYPES:
            continue
        checked += 1
        tags = resource.get("tags")
        # `tags: managementLayerTags` は ARM 上 "[variables('...')]" になる。
        if isinstance(tags, str):
            ref = re.fullmatch(r"\[variables\('([^']+)'\)\]", tags.strip())
            tags = variables.get(ref.group(1)) if ref else None
        if not isinstance(tags, dict):
            missing.append(resource_type)
            continue
        values = {str(k).lower(): str(v) for k, v in tags.items()}
        if values.get(LAYER_TAG_KEY) != LAYER_TAG_MANAGEMENT_VALUE:
            wrong.append(f"{resource_type} -> {values.get(LAYER_TAG_KEY)!r}")

    return wrong, missing, checked


@pytest.mark.skipif(
    _bicep_command() is None,
    # スキップは「通った」ではない: bicep の無い環境では**評価後の値は未検証**。
    # 同じ検査を iac-validate.yml (bicep あり) が pytest で走らせ、
    # そこで skip が出たら job を落とす (未検証のまま緑にしない)。
    reason="bicep / az が無いので ARM JSON を作れない (この層は未検証 / iac-validate が担当)",
)
def test_compiled_arm_resources_carry_the_layer_tag() -> None:
    """評価後の ARM でも層タグが一致しているか。

    無いと: `union()` や条件式でタグを組み立てるように書き換えたとき、
    ソースの見た目は正しいのに**評価後の値が別物**になる経路が塞げない。
    """
    wrong, missing, checked = _arm_layer_tag_findings(_compile_arm())
    assert checked > 0, "ARM にタグ対象の resource が 1 つもありません (照合の空振り)"
    assert not wrong, f"層タグの値がズレている resource: {wrong}"
    assert not missing, f"層タグを持たない resource: {missing}"
