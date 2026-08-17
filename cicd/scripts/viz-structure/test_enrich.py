"""[L2] 構成図の論理エッジ推定と役割表 — 図の「嘘」と「孤立ノード」を再発させない。

無いと何が静かに通るか:
    役割表の説明文は Azure から取れない (エージェントが書いた地の文) ので、実装が進化しても
    誰も気づかず古いまま公開 docs に載り続ける。実際に SWA の説明が「Standard SKU +
    linked-backend」のまま ADR 0013 (Free + 直叩き) と半年矛盾し、Cosmos は
    「Role not yet classified」、Speech は「LLM endpoint」と誤記されていた。
    エッジも同様: heuristic が全 cognitiveservices に線を引く実装だったため
    「ai-agent → Speech」という存在しない依存が図に描かれていた。
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import enrich  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).parents[3]

RG = "rg-dev-mind-inbox"


def _node(id_, name, type_):
    return {
        "id": id_,
        "label": f"{name}\n{type_.split('/')[-2]}/{type_.split('/')[-1]}",
        "type": type_,
        "rg": RG,
        "sub": "00000000-0000-0000-0000-000000000000",
        "subnet": None,
        "vnet": None,
    }


def dev_env_nodes():
    """実 dev 環境 (infra_arch_resource_roles.md) と同じ顔ぶれのフィクスチャ。"""
    return [
        _node("id-swa", "swa-dev-mindbox", "microsoft.web/staticsites"),
        _node("id-func", "func-dev-mindbox", "microsoft.web/sites"),
        _node("id-asp", "asp-dev-mindbox-func", "microsoft.web/serverfarms"),
        _node("id-cae", "cae-dev-mindbox", "microsoft.app/managedenvironments"),
        _node("id-ai", "ca-dev-mindbox-ai-agent", "microsoft.app/containerapps"),
        _node("id-vv", "ca-dev-mindbox-voicevox", "microsoft.app/containerapps"),
        _node("id-vvw", "ca-dev-mindbox-vv-wrap", "microsoft.app/containerapps"),
        _node("id-oai", "oai-dev-mindbox", "microsoft.cognitiveservices/accounts"),
        _node("id-spch", "spch-dev-mindbox", "microsoft.cognitiveservices/accounts"),
        _node("id-cosmos", "cosmos-dev-mindbox", "microsoft.documentdb/databaseaccounts"),
        _node("id-vnet", "vnet-dev-mindbox", "microsoft.network/virtualnetworks"),
        _node("id-law", "law-dev-mindbox-ops", "microsoft.operationalinsights/workspaces"),
        _node("id-st", "stdevmindboxfunc", "microsoft.storage/storageaccounts"),
        _node("id-appi", "appi-dev-mindbox", "microsoft.insights/components"),
        _node("id-ag-smart", "Application Insights Smart Detection", "microsoft.insights/actiongroups"),
    ]


def edge_pairs(edges):
    return {(e["from"], e["to"]) for e in edges}


def test_logical_edges_wire_bff_to_cosmos_and_speech_but_not_ai_agent_to_speech():
    """[L2] 実リポジトリのコードを根拠にエッジを推定する (根拠ファイルが動いたら落ちて知らせる)。"""
    pairs = edge_pairs(enrich.build_logical_edges(dev_env_nodes(), REPO_ROOT))

    # BFF が実際に呼ぶ相手 (ADR 0030 / ADR 0023)
    assert ("id-func", "id-cosmos") in pairs
    assert ("id-func", "id-spch") in pairs
    assert ("id-func", "id-ai") in pairs
    assert ("id-func", "id-vvw") in pairs
    # AI Agent が呼ぶのは OpenAI だけ。Speech に線を引いた旧バグの再発防止
    assert ("id-ai", "id-oai") in pairs
    assert ("id-ai", "id-spch") not in pairs


def test_browser_pseudo_node_connects_swa_func_and_speech():
    """[L2] SWA は linked backend を使わない (ADR 0013) ので、ブラウザ擬似ノードが
    無いと図の上で孤立する。ブラウザ → SWA / BFF / Speech の 3 本で入口を描く。"""
    browser, edges = enrich.build_browser_node_and_edges(dev_env_nodes(), REPO_ROOT)

    assert browser is not None
    assert browser["external"] is True
    pairs = edge_pairs(edges)
    assert (browser["id"], "id-swa") in pairs
    assert (browser["id"], "id-func") in pairs
    assert (browser["id"], "id-spch") in pairs


def test_role_notes_match_current_adrs():
    """[L1] 役割表の説明が現行 ADR と矛盾しない (SWA=0013 / Cosmos=0030 / Speech=0023)。"""
    nodes = {n["id"]: n for n in dev_env_nodes()}

    swa_role, swa_note = enrich.role_for(nodes["id-swa"])
    assert "Standard SKU" not in swa_note
    assert "no linked backend" in swa_note
    assert "directly" in swa_note

    cosmos_role, cosmos_note = enrich.role_for(nodes["id-cosmos"])
    assert cosmos_role != "General Azure resource"
    assert "Cosmos" in cosmos_role or "Cosmos" in cosmos_note

    spch_role, spch_note = enrich.role_for(nodes["id-spch"])
    assert "LLM" not in spch_role and "LLM" not in spch_note
    assert "STT" in spch_role or "speech-to-text" in spch_note


def test_app_insights_and_smart_detection_are_classified():
    """[単体] appi (App Insights) と Smart Detection action group が
    「Role not yet classified」のまま公開 docs に載り続けない (#478)。"""
    nodes = {n["id"]: n for n in dev_env_nodes()}

    appi_role, appi_note = enrich.role_for(nodes["id-appi"])
    assert appi_role != "General Azure resource"
    # 根拠を指す: BFF 専用 (Functions ホストの自動収集 / ADR 0055) で、
    # 宣言は main-bootstrap.bicep の enableAppInsights。AI Agent は exporter 未配線
    # (#463) なので「OTel の出口」と書いたら嘘になる
    assert "BFF" in appi_role or "BFF" in appi_note
    assert "enableAppInsights" in appi_note and "main-bootstrap.bicep" in appi_note
    assert "ADR 0055" in appi_note
    assert "OTel" not in appi_note

    ag_role, ag_note = enrich.role_for(nodes["id-ag-smart"])
    assert ag_role != "General Azure resource"
    assert "Smart Detection" in ag_note


def test_icon_warnings_flag_unregistered_type_and_missing_png():
    """[単体] アイコンを出せないノードの警告判定 (純粋関数)。無いと何が静かに通るか:
    ICON_MAP 未登録 / PNG 未配置の劣化が生成ログに出ず、素の箱だらけの図が
    公開 docs を黙って上書きする (2026-08-09 のアイコン全滅事故の再発経路)。"""
    nodes = dev_env_nodes()
    nodes.append(_node("id-ds", "ds-entra-auth", "microsoft.resources/deploymentscripts"))
    browser = dict(_node("id-browser", "User (Browser)", "external/client"), external=True)
    nodes.append(browser)

    warnings = "\n".join(enrich.icon_warnings(nodes, ["function-app.png"]))

    # ICON_MAP に無い種別 → 「未登録」
    assert "アイコン未登録: microsoft.resources/deploymentscripts" in warnings
    # 登録済みだが PNG 実体が無い → 「PNG 未配置」(#478 の 2 種別で確認)
    assert "アイコン PNG 未配置: app-insights.png" in warnings
    assert "アイコン PNG 未配置: action-group.png" in warnings
    # PNG があるものと external 擬似ノードは警告しない
    assert "function-app.png" not in warnings
    assert "external/client" not in warnings


def test_icon_map_covers_dev_env_types():
    """[単体] 実 dev 環境の顔ぶれは全種別が ICON_MAP 登録済み。無いと何が静かに通るか:
    「アイコン未登録」警告が定常ログ化し、本当に新種別が増えたときに埋もれる。"""
    for n in dev_env_nodes():
        assert enrich.lc(n["type"]) in enrich.ICON_MAP, n["type"]


def test_cross_rg_pairs_are_not_wired():
    """[L2] 複数 RG を 1 枚の図にしたとき、環境をまたぐ偽の依存線を作らない
    (dev の BFF → stg の Cosmos のような線が出たら、図が嘘をつく)。"""
    nodes = dev_env_nodes()
    other = [dict(n, id=n["id"] + "-stg", rg="rg-stg-mind-inbox") for n in nodes]
    pairs = edge_pairs(enrich.build_logical_edges(nodes + other, REPO_ROOT))

    assert ("id-func", "id-cosmos") in pairs
    assert ("id-func-stg", "id-cosmos-stg") in pairs
    assert ("id-func", "id-cosmos-stg") not in pairs
    assert ("id-func-stg", "id-cosmos") not in pairs


def test_vnet_and_swa_notes_follow_deployed_state():
    """[L2] VNet「未使用」と SWA「直叩き」は決め打ちせず、グラフの実データで判定する
    (SQL 有効環境の VNet を unused と書いたり、linked backend 有効の SWA を
    直叩きと書いたりする嘘を防ぐ)。"""
    nodes = dev_env_nodes()

    # 何も参照されない VNet → unused と明記
    rows = {r["name"]: r for r in enrich.build_role_rows(nodes, [])}
    assert "unused" in rows["vnet-dev-mindbox"]["role"]
    assert "ADR 0013" in rows["swa-dev-mindbox"]["note"]

    # subnet に居るリソースがある VNet → unused と書かない。
    # linked backend の構造エッジがある SWA → 直叩きの既定文を上書き
    attached = [dict(n, vnet="id-vnet") if n["id"] == "id-func" else n for n in nodes]
    linked_edge = [{"from": "id-swa", "to": "id-func", "rel": "linkedBackend"}]
    rows = {r["name"]: r for r in enrich.build_role_rows(attached, linked_edge)}
    assert "unused" not in rows["vnet-dev-mindbox"]["role"]
    assert "linked backend" in rows["swa-dev-mindbox"]["note"]


def test_main_writes_classified_roles_and_excludes_browser(tmp_path):
    """[L2] 一気通貫: 現行 dev 環境の顔ぶれなら全リソースが分類済みで、
    ブラウザ擬似ノードは graph.json には載るが役割表には載らない。"""
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps({"nodes": dev_env_nodes(), "edges": []}), encoding="utf-8")
    logical = tmp_path / "logical-edges.json"
    tsv = tmp_path / "roles.tsv"
    md = tmp_path / "roles.md"

    enrich.main(["enrich.py", str(graph_path), str(REPO_ROOT), str(logical), str(tsv), str(md)])

    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert any(n.get("external") for n in graph["nodes"])

    roles_md = md.read_text(encoding="utf-8")
    assert "Role not yet classified" not in roles_md
    assert "Browser" not in roles_md

    edges = json.loads(logical.read_text(encoding="utf-8"))
    assert any(e["rel"] == "cosmosData" for e in edges)
    assert any(e["rel"] == "spaAssets" for e in edges)
