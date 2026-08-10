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
    assert "linked-backend" not in swa_note
    assert "Standard SKU" not in swa_note
    assert "Free" in swa_note

    cosmos_role, cosmos_note = enrich.role_for(nodes["id-cosmos"])
    assert cosmos_role != "General Azure resource"
    assert "Cosmos" in cosmos_role or "Cosmos" in cosmos_note

    spch_role, spch_note = enrich.role_for(nodes["id-spch"])
    assert "LLM" not in spch_role and "LLM" not in spch_note
    assert "STT" in spch_role or "speech-to-text" in spch_note

    vnet_role, vnet_note = enrich.role_for(nodes["id-vnet"])
    assert "unused" in vnet_role or "nothing references it" in vnet_note


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
