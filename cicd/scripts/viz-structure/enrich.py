#!/usr/bin/env python3
"""graph.json をコードベース由来の情報で豊かにする (viz-structure.sh の step 2.5)。

やること 3 つ:
1. 論理エッジ推定 — Azure Resource Graph には現れない「コードが実際に呼ぶ」関係を
   リポジトリの実ファイルを grep して推定する (BFF→AI Agent / BFF→Cosmos など)。
2. 外部クライアント (ブラウザ) の擬似ノード — SWA は linked backend を使わない設計
   (ADR 0013) なので Azure リソース同士の構造エッジが存在しない。実際の流れは
   「ブラウザが SWA から SPA をロード → BFF を直叩き → Speech へ WebSocket」なので、
   ブラウザを図に描かないと SWA が孤立して見える。
3. リソース役割表 (resource-roles.md / .tsv) の生成。

viz-structure.sh から呼ばれる。単体でも pytest から import してテストできる。
"""

import json
import pathlib
import re
import sys

BROWSER_NODE_ID = "external/browser"


def lc(s):
    return (s or "").lower()


def node_name(node):
    return lc((node.get("label") or "").split("\n")[0])


def find_nodes(nodes, type_lc=None, name_contains=None):
    out = []
    for n in nodes:
        if type_lc and lc(n.get("type")) != type_lc:
            continue
        if name_contains and name_contains not in node_name(n):
            continue
        out.append(n)
    return out


def read_text_safe(path):
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def grep_any(paths, patterns):
    regex = re.compile("|".join(patterns), re.IGNORECASE)
    for p in paths:
        if not p.exists() or not p.is_file():
            continue
        txt = read_text_safe(p)
        if txt and regex.search(txt):
            return True
    return False


def _edges(srcs, dsts, rel):
    return [
        {"from": s.get("id"), "to": d.get("id"), "rel": rel, "source": "codebase"}
        for s in srcs
        for d in dsts
    ]


def build_logical_edges(nodes, codebase_root):
    """コードの実ファイルを根拠に論理エッジを推定する。根拠ファイルが消えたら線も消える。"""
    root = pathlib.Path(codebase_root)
    logical_edges = []

    # AI Agent Container App -> Azure OpenAI account。
    # cognitiveservices は Speech (spch-) も同型なので、OpenAI アカウント (oai-) に限定する。
    if grep_any(
        [
            root / "apps/services/ai-agent/README.md",
            root / "cicd/scripts/deploy/deploy-ai-agent.sh",
        ],
        [r"AZURE_OPENAI_ENDPOINT", r"openai\.azure\.com", r"openAiEndpoint"],
    ):
        logical_edges += _edges(
            find_nodes(nodes, "microsoft.app/containerapps", "ai-agent"),
            find_nodes(nodes, "microsoft.cognitiveservices/accounts", "oai"),
            "openaiApi",
        )

    # VOICEVOX Wrapper Container App -> VOICEVOX Engine Container App
    if grep_any(
        [
            root / "apps/services/voicevox/README.md",
            root / "cicd/scripts/deploy/deploy-voicevox-wrapper.sh",
        ],
        [r"VOICEVOX_ENGINE_BASE_URL", r"voicevox"],
    ):
        engines = [
            n
            for n in find_nodes(nodes, "microsoft.app/containerapps", "voicevox")
            if "vv-wrap" not in node_name(n)
        ]
        logical_edges += _edges(
            find_nodes(nodes, "microsoft.app/containerapps", "vv-wrap"),
            engines,
            "voicevoxEngine",
        )

    # Function App (BFF) -> VOICEVOX Wrapper API
    if grep_any(
        [
            root / "apps/bff/src/config.ts",
            root / "apps/bff/src/clients/voicevoxClient.ts",
        ],
        [r"VOICEVOX_BASE_URL", r"/synthesize", r"voicevox"],
    ):
        logical_edges += _edges(
            find_nodes(nodes, "microsoft.web/sites", "func-"),
            find_nodes(nodes, "microsoft.app/containerapps", "vv-wrap"),
            "voicevoxApi",
        )

    # Function App (BFF) -> AI Agent Container App
    if grep_any(
        [
            root / "apps/bff/src/config.ts",
            root / "apps/bff/src/clients/aiAgentClient.ts",
            root / "apps/bff/src/trpc/routers/chat.ts",
        ],
        [r"AI_AGENT_BASE_URL", r"aiAgentClient", r"ai-agent"],
    ):
        logical_edges += _edges(
            find_nodes(nodes, "microsoft.web/sites", "func-"),
            find_nodes(nodes, "microsoft.app/containerapps", "ai-agent"),
            "aiAgentApi",
        )

    # Function App (BFF) -> Cosmos DB (ADR 0030: 永続化は BFF の内側の Cosmos 1 本)
    if grep_any(
        [
            root / "apps/bff/src/config.ts",
            root / "apps/bff/src/repositories/cosmosClient.ts",
        ],
        [r"COSMOS_ENDPOINT", r"cosmosClient"],
    ):
        logical_edges += _edges(
            find_nodes(nodes, "microsoft.web/sites", "func-"),
            find_nodes(nodes, "microsoft.documentdb/databaseaccounts", None),
            "cosmosData",
        )

    # Function App (BFF) -> Azure Speech (ADR 0023: BFF は MI で STT トークンを発行するだけ)
    if grep_any(
        [
            root / "apps/bff/src/config.ts",
            root / "apps/bff/src/clients/speechTokenClient.ts",
        ],
        [r"SPEECH_RESOURCE_ID", r"issueToken"],
    ):
        logical_edges += _edges(
            find_nodes(nodes, "microsoft.web/sites", "func-"),
            find_nodes(nodes, "microsoft.cognitiveservices/accounts", "spch"),
            "speechToken",
        )

    node_ids = {n.get("id") for n in nodes}
    logical_edges = [
        e
        for e in logical_edges
        if e.get("from") in node_ids and e.get("to") in node_ids and e.get("from") and e.get("to")
    ]

    unique = {}
    for e in logical_edges:
        key = (e["from"], e["to"])
        if key not in unique:
            unique[key] = e
    return list(unique.values())


def build_browser_node_and_edges(nodes, codebase_root):
    """ブラウザ擬似ノードと、そこから出るエッジを作る。

    SWA と Functions の間に Azure 上の構造エッジは無い (linked backend 不使用 / ADR 0013)。
    ユーザーの入口であるブラウザを描かないと SWA が孤立ノードに見えるため、
    external フラグ付きの擬似ノードとして補う (役割表からは除外される)。
    """
    root = pathlib.Path(codebase_root)
    edges = []

    swas = find_nodes(nodes, "microsoft.web/staticsites", None)
    funcs = find_nodes(nodes, "microsoft.web/sites", "func-")
    spchs = find_nodes(nodes, "microsoft.cognitiveservices/accounts", "spch")

    browser = {
        "id": BROWSER_NODE_ID,
        "label": "User (Browser)\nexternal/client",
        "type": "external/client",
        "rg": None,
        "sub": None,
        "subnet": None,
        "vnet": None,
        "external": True,
    }

    # ブラウザ -> SWA: SPA の配信元 (SWA が居るなら常に成立する関係)
    edges += _edges([browser], swas, "spaAssets")

    # ブラウザ -> BFF: ビルド時に焼き込まれた VITE_BFF_BASE_URL で Functions を直叩き (#69)
    if grep_any(
        [root / "apps/frontend/src/trpc/client.ts"],
        [r"VITE_BFF_BASE_URL"],
    ):
        edges += _edges([browser], funcs, "trpcApi")

    # ブラウザ -> Azure Speech: BFF 発行のトークンで WebSocket ストリーミング STT (ADR 0023)
    if grep_any(
        [
            root / "apps/frontend/src/voice/azureSpeech.ts",
            root / "apps/frontend/package.json",
        ],
        [r"cognitiveservices-speech-sdk"],
    ):
        edges += _edges([browser], spchs, "speechStt")

    if not edges:
        return None, []
    return browser, edges


ROLE_MAP = {
    "microsoft.web/staticsites": ("Frontend hosting", "Browser UI delivery"),
    "microsoft.web/sites": ("Backend API", "Hosts Azure Functions/BFF endpoints"),
    "microsoft.web/serverfarms": ("Compute plan", "Function/App Service compute capacity"),
    "microsoft.storage/storageaccounts": ("Function runtime storage", "Functions runtime/state storage"),
    "microsoft.operationalinsights/workspaces": ("Central logging", "Aggregates app/platform logs and metrics"),
    "microsoft.network/virtualnetworks": ("Network boundary", "Private address space and subnet isolation"),
    "microsoft.network/privateendpoints": ("Private ingress", "Private connectivity to PaaS resources"),
    "microsoft.network/privatednszones": ("Private name resolution", "DNS for private endpoints"),
    "microsoft.network/privatednszones/virtualnetworklinks": ("DNS-to-VNet link", "Binds private DNS zone to VNet"),
    "microsoft.sql/servers": ("Relational DB server", "Managed SQL control plane"),
    "microsoft.sql/servers/databases": ("Application database", "Persistent relational data"),
    "microsoft.keyvault/vaults": ("Secrets management", "Stores/guards credentials and secrets"),
    "microsoft.cognitiveservices/accounts": ("Cognitive Services account", "Azure AI service endpoint"),
    "microsoft.cognitiveservices/accounts/deployments": ("Model deployment", "Specific model/runtime deployment"),
    "microsoft.documentdb/databaseaccounts": ("Document store", "Cosmos DB account"),
    "microsoft.containerregistry/registries": ("Container image registry", "Stores deployable images"),
    "microsoft.app/managedenvironments": ("Container app environment", "Shared runtime/network for Container Apps"),
    "microsoft.app/containerapps": ("App microservice", "Runtime endpoint for service workloads"),
}

# Per-resource overrides: (type_lc, name_substring_lc, role, note)
# Resolution: first match wins — list more specific patterns BEFORE general ones.
NAME_OVERRIDES = [
    # Container Apps: 3 services, distinct roles
    ("microsoft.app/containerapps", "ai-agent", "AI Agent service",
        "FastAPI + Semantic Kernel; orchestrates Azure OpenAI calls and human-in-the-loop tool approval"),
    ("microsoft.app/containerapps", "vv-wrap", "VOICEVOX wrapper API",
        "FastAPI; bridges BFF and the VOICEVOX engine, handles audio post-processing"),
    ("microsoft.app/containerapps", "voicevox", "VOICEVOX TTS engine",
        "Speech synthesis runtime (open-source VOICEVOX engine)"),
    # Managed environments: name-based hint about which CA group they host
    ("microsoft.app/managedenvironments", "ai", "Container app env (AI Agent)",
        "Hosts AI Agent Container App"),
    ("microsoft.app/managedenvironments", "vv-wrap", "Container app env (VOICEVOX wrap)",
        "Hosts VOICEVOX wrapper Container App"),
    ("microsoft.app/managedenvironments", "voicevox", "Container app env (VOICEVOX engine)",
        "Hosts VOICEVOX engine Container App"),
    # Web tier
    ("microsoft.web/sites", "func-", "BFF (Azure Functions + tRPC)",
        "Single tRPC entrypoint; orchestrates AI Agent / VOICEVOX wrapper, NOT a chat passthrough"),
    ("microsoft.web/staticsites", "swa-", "Frontend SPA (React + Vite + MUI)",
        "SWA Free SKU serving the SPA bundle; the browser calls the BFF Function App "
        "directly (no linked backend), auth enforced by Functions EasyAuth 401 (ADR 0013)"),
    ("microsoft.web/serverfarms", "asp-", "Function App Service plan",
        "Compute capacity for the BFF Function App"),
    # Data & secrets
    ("microsoft.documentdb/databaseaccounts", "cosmos-", "Problem persistence store (Cosmos DB)",
        "Single persistence store behind the BFF for Problem / history data; "
        "short-lived items expire via native TTL (ADR 0030)"),
    ("microsoft.sql/servers/databases", "master", "(SQL system DB)",
        "Default master DB; not used by the application"),
    ("microsoft.sql/servers/databases", "sqldb-", "Application database (provisioned, not wired)",
        "Provisioned for future session/artifact persistence; BFF and AI Agent currently do not reference it"),
    ("microsoft.sql/servers", "sql-", "Relational DB server (provisioned, not wired)",
        "SQL infra is fully set up (server + DB + private endpoint + DNS) but no application code references it yet"),
    ("microsoft.keyvault/vaults", "kv-", "Secrets management (SQL creds)",
        "Stores SQL admin password and other deployment secrets"),
    # AI services: oai- は LLM 推論、spch- は STT (ADR 0023)。取り違えやすいので明示する
    ("microsoft.cognitiveservices/accounts", "oai-", "Azure OpenAI account",
        "GPT-4o for AI Agent inference"),
    ("microsoft.cognitiveservices/accounts", "spch-", "Azure Speech (STT)",
        "Server-side speech-to-text (F0). BFF issues short-lived tokens via managed identity; "
        "the browser streams audio to Speech directly over WebSocket (ADR 0023)"),
    # Networking specifics
    ("microsoft.network/virtualnetworks", "vnet-", "Network boundary (unused when both SQL and VNet integration are off)",
        "Only consumed by the SQL private endpoint (enableSql) or Functions VNet integration "
        "(enableFunctionVnetIntegration); with both flags off nothing references it"),
    ("microsoft.network/privateendpoints", "pe-sql", "Private endpoint to SQL",
        "Network-isolated SQL access from within VNet"),
    ("microsoft.network/networkinterfaces", "pe-sql", "Private endpoint NIC (SQL)",
        "Auto-created NIC for SQL private endpoint"),
    ("microsoft.network/privatednszones", "privatelink.database.windows.net", "Private DNS zone (SQL)",
        "Resolves SQL private endpoint FQDN inside the VNet"),
    ("microsoft.containerregistry/registries", "cr", "Container image registry",
        "Stores AI Agent / VOICEVOX wrapper images for Container Apps"),
    ("microsoft.storage/storageaccounts", "st", "Function runtime storage",
        "Required by Azure Functions for state/queues/triggers"),
    ("microsoft.operationalinsights/workspaces", "law-", "Central Log Analytics workspace",
        "Aggregates logs/metrics from BFF, Container Apps, and platform"),
    # Bicep deployment artifacts (transient — note this so reader doesn't mistake them for app components)
    ("microsoft.resources/deploymentscripts", "ds-", "Bicep deployment script (transient)",
        "Generated by IaC for one-shot tasks (e.g., Entra ID auth setup); safe to ignore in arch view"),
]


def role_for(node):
    t = lc(node.get("type"))
    name = node_name(node)
    for pat_t, pat_n, pat_role, pat_note in NAME_OVERRIDES:
        if t == pat_t and pat_n in name and pat_role is not None:
            return (pat_role, pat_note)
    return ROLE_MAP.get(t, ("General Azure resource", "Role not yet classified"))


def build_role_rows(nodes):
    rows = []
    for n in sorted(nodes, key=lambda x: (lc(x.get("rg")), lc(x.get("type")), lc(x.get("label")))):
        if n.get("external"):
            continue
        role, note = role_for(n)
        rows.append(
            {
                "name": (n.get("label", "").split("\n")[0] if n.get("label") else ""),
                "type": n.get("type", ""),
                "resourceGroup": n.get("rg", ""),
                "role": role,
                "note": note,
                "id": n.get("id", ""),
            }
        )
    return rows


def main(argv):
    graph_path = pathlib.Path(argv[1])
    codebase_root = pathlib.Path(argv[2])
    logical_edges_path = pathlib.Path(argv[3])
    roles_tsv_path = pathlib.Path(argv[4])
    roles_md_path = pathlib.Path(argv[5])

    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    nodes = graph.get("nodes", [])

    logical_edges = build_logical_edges(nodes, codebase_root)

    browser, browser_edges = build_browser_node_and_edges(nodes, codebase_root)
    if browser is not None:
        graph["nodes"] = nodes + [browser]
        logical_edges += browser_edges
        graph_path.write_text(
            json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    logical_edges_path.write_text(
        json.dumps(logical_edges, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    rows = build_role_rows(graph["nodes"] if browser is not None else nodes)

    with roles_tsv_path.open("w", encoding="utf-8") as f:
        f.write("name\ttype\tresourceGroup\trole\tnote\tid\n")
        for r in rows:
            f.write(
                f"{r['name']}\t{r['type']}\t{r['resourceGroup']}\t{r['role']}\t{r['note']}\t{r['id']}\n"
            )

    with roles_md_path.open("w", encoding="utf-8") as f:
        f.write("# Resource Roles\n\n")
        f.write("| Name | Type | RG | Role | Note |\n")
        f.write("|---|---|---|---|---|\n")
        for r in rows:
            f.write(
                f"| {r['name']} | {r['type']} | {r['resourceGroup']} | {r['role']} | {r['note']} |\n"
            )


if __name__ == "__main__":
    main(sys.argv)
