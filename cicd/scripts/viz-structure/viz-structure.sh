#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./viz-structure.sh --subs "<subIdOrName1,subIdOrName2>" --rgs "rg-a,rg-b" --out artifacts/infra_arch/latest
#
# Notes:
# - Edges are intentionally minimal at first (grouping only).
# - You can extend edge rules later by enriching graph.json.

HERE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT="$(git -C "$HERE_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$PROJECT_ROOT" ]]; then
  PROJECT_ROOT="$(cd -- "$HERE_DIR/../../.." && pwd)"
fi

resolve_path() {
  local p="$1"
  if [[ "$p" = /* ]]; then
    printf '%s' "$p"
  elif [[ "$p" == */* ]]; then
    printf '%s/%s' "$PROJECT_ROOT" "$p"
  else
    printf '%s/%s' "$HERE_DIR" "$p"
  fi
}

resolve_project_path() {
  local p="$1"
  if [[ "$p" = /* ]]; then
    printf '%s' "$p"
  else
    printf '%s/%s' "$PROJECT_ROOT" "$p"
  fi
}

ARCH_FILE_NAME="infra_arch"
SUBS=""
RGS=""
OUT="artifacts/$ARCH_FILE_NAME/latest"
ICONS_DIR=""
DEFAULT_ICONS_DIR="$HERE_DIR/icons"
DOCS_DIR="$PROJECT_ROOT/docs/cicd/iac"
CODEBASE_ROOT="$PROJECT_ROOT"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subs) SUBS="$2"; shift 2;;
    --rgs)  RGS="$2"; shift 2;;
    --out)  OUT="$2"; shift 2;;
    --icons) ICONS_DIR="$2"; shift 2;;
    --codebase-root) CODEBASE_ROOT="$2"; shift 2;;
    *) echo "Unknown arg: $1" >&2; exit 1;;
  esac
done

OUT="$(resolve_path "$OUT")"
if [[ -n "$ICONS_DIR" ]]; then
  ICONS_DIR="$(resolve_path "$ICONS_DIR")"
fi
CODEBASE_ROOT="$(resolve_project_path "$CODEBASE_ROOT")"

if [[ -z "$SUBS" || -z "$RGS" ]]; then
  echo "Required: --subs and --rgs" >&2
  exit 1
fi

if [[ -n "$ICONS_DIR" && ! -d "$ICONS_DIR" ]]; then
  echo "WARN: --icons dir not found: $ICONS_DIR (icons disabled)" >&2
  ICONS_DIR=""
fi

# Auto-enable local icons if present.
if [[ -z "$ICONS_DIR" && -d "$DEFAULT_ICONS_DIR" ]]; then
  if compgen -G "$DEFAULT_ICONS_DIR/*.png" >/dev/null; then
    ICONS_DIR="$DEFAULT_ICONS_DIR"
    echo "INFO: using local icons dir: $ICONS_DIR"
  fi
fi

command -v az >/dev/null || { echo "az not found" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found" >&2; exit 1; }
command -v dot >/dev/null || { echo "dot (graphviz) not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }

mkdir -p "$OUT"
mkdir -p "$DOCS_DIR"

if [[ ! -d "$CODEBASE_ROOT" ]]; then
  echo "WARN: --codebase-root not found: $CODEBASE_ROOT (logical code deps disabled)" >&2
  CODEBASE_ROOT=""
fi

# 1) Collect (Azure Resource Graph)
# Fetch basic fields + properties (needed to infer edges).
QUERY=$(cat <<'KQL'
Resources
| project id, name, type, resourceGroup, subscriptionId, location, properties
KQL
)

# Convert comma lists to ARG arrays
IFS=',' read -r -a SUB_ARR <<< "$SUBS"

trim() {
  local s="$1"
  # trim leading/trailing whitespace
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

is_guid() {
  local s="$1"
  [[ "$s" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]
}

resolve_sub_id() {
  local sub_in
  sub_in="$(trim "$1")"
  if [[ -z "$sub_in" ]]; then
    return 1
  fi
  if is_guid "$sub_in"; then
    printf '%s' "$sub_in"
    return 0
  fi

  # Accept subscription name or any selector az understands.
  # This returns the GUID subscription id.
  local sub_id
  sub_id="$(az account show --subscription "$sub_in" --query id -o tsv 2>/dev/null || true)"
  if [[ -z "$sub_id" ]]; then
    echo "ERROR: Could not resolve subscription '$sub_in' to a subscription id." >&2
    echo "- Ensure you are logged in: az login" >&2
    echo "- Ensure the subscription is accessible: az account list -o table" >&2
    return 1
  fi
  printf '%s' "$sub_id"
}

SUB_IDS=()
for s in "${SUB_ARR[@]}"; do
  s="$(trim "$s")"
  [[ -z "$s" ]] && continue
  SUB_IDS+=("$(resolve_sub_id "$s")")
done

if [[ ${#SUB_IDS[@]} -eq 0 ]]; then
  echo "ERROR: --subs did not contain any valid subscription entries." >&2
  exit 1
fi

# Resource Graph query (across subscriptions)
# Note: We filter by resourceGroup client-side to keep query simple.
az graph query -q "$QUERY" --subscriptions "${SUB_IDS[@]}" -o json \
  | jq --arg rgs "$RGS" '
      ($rgs | split(",") | map(ascii_downcase)) as $rgset
      | .data
      | map(select((.resourceGroup // "" | ascii_downcase) as $rg | ($rgset | index($rg))))
    ' > "$OUT/resources.json"

# 2) Normalize to graph.json (nodes + inferred edges)
# Node id: Azure resource id
# label: short name + type
jq '
  def lc: ascii_downcase;

  def subnet_id($o):
    (
      if ($o.properties.subnet.id? and ($o.properties.subnet.id|type)=="string") then $o.properties.subnet.id
      elif ($o.properties.ipConfigurations?[0].properties.subnet.id? and ($o.properties.ipConfigurations[0].properties.subnet.id|type)=="string") then $o.properties.ipConfigurations[0].properties.subnet.id
      else empty
      end
    );

  def vnet_id_from_subnet($sid):
    ($sid | sub("/subnets/[^/]+$"; ""));

  def type_short($t):
    ($t | split("/") as $p
     | if ($p|length) >= 2 then ($p[-2] + "/" + $p[-1]) else $t end);

  def mk_node:
    {
      id: .id,
      label: (.name + "\n" + (type_short(.type))),
      type: .type,
      rg: .resourceGroup,
      sub: .subscriptionId,
      subnet: (subnet_id(.) // null),
      vnet: ((subnet_id(.) | vnet_id_from_subnet(.)) //
            (if ((.type|lc) == "microsoft.network/virtualnetworks") then .id else null end))
    };

  def edges_from_private_endpoints($items):
    ($items
      | map(select((.type|lc) == "microsoft.network/privateendpoints"))
      | map(. as $pe
          | (($pe.properties.privateLinkServiceConnections // [])
              | map({
                  from: $pe.id,
                  to: (.properties.privateLinkServiceId // empty),
                  rel: "privateLink"
                })
            )
        )
      | add // []
    );

  def edges_from_web_sites($items):
    ($items
      | map(select((.type|lc) == "microsoft.web/sites"))
      | map(. as $site
          | if ($site.properties.serverFarmId? and ($site.properties.serverFarmId|type) == "string" and ($site.properties.serverFarmId|length) > 0)
            then [{ from: $site.id, to: $site.properties.serverFarmId, rel: "serverFarm" }]
            else []
            end
        )
      | add // []
    );

  def edges_from_vnet_links($items):
    ($items
      | map(select((.type|lc) == "microsoft.network/privatednszones/virtualnetworklinks"))
      | map(. as $lnk
          | if ($lnk.properties.virtualNetwork.id? and ($lnk.properties.virtualNetwork.id|type) == "string" and ($lnk.properties.virtualNetwork.id|length) > 0)
            then [{ from: $lnk.id, to: $lnk.properties.virtualNetwork.id, rel: "virtualNetwork" }]
            else []
            end
        )
      | add // []
    );

  def edges_from_sql_dbs($items):
    ($items
      | map(select((.type|lc) == "microsoft.sql/servers/databases"))
      | map(select((.id | test("/databases/master$") | not)))
      | map({
          from: .id,
          to: (.id | sub("/databases/[^/]+$"; "")),
          rel: "server"
        })
    );

  def edges_from_static_sites($items):
    ($items
      | map(select((.type|lc) == "microsoft.web/staticsites"))
      | map(. as $swa
          | (($swa.properties.linkedBackends // [])
              | map({
                  from: $swa.id,
                  to: (.backendResourceId // empty),
                  rel: "linkedBackend"
                })
            )
        )
      | add // []
    );

  def edges_from_container_apps($items):
    ($items
      | map(select((.type|lc) == "microsoft.app/containerapps"))
      | map(. as $app
          | ((
              if ($app.properties.managedEnvironmentId? and ($app.properties.managedEnvironmentId|type) == "string" and ($app.properties.managedEnvironmentId|length) > 0)
              then $app.properties.managedEnvironmentId
              elif ($app.properties.environmentId? and ($app.properties.environmentId|type) == "string" and ($app.properties.environmentId|length) > 0)
              then $app.properties.environmentId
              else empty
              end
            )) as $envId
          | [{
              from: $app.id,
              to: $envId,
              rel: "managedEnvironment"
            }]
        )
      | add // []
    );

  . as $items
  | ($items | map({ key: (.id|lc), value: .id }) | from_entries) as $idmap
  | {
      nodes: ($items | map(mk_node)),
      edges: (
        (
          edges_from_private_endpoints($items)
          + edges_from_web_sites($items)
          + edges_from_vnet_links($items)
          + edges_from_sql_dbs($items)
          + edges_from_static_sites($items)
          + edges_from_container_apps($items)
        )
        | map(select(.to != null and (.to|type) == "string" and (.to|length) > 0))
        | map(. as $e
            | select(($idmap[($e.from|lc)] != null) and ($idmap[($e.to|lc)] != null))
            | {
                from: ($idmap[($e.from|lc)] // $e.from),
                to: ($idmap[($e.to|lc)] // $e.to),
                rel: $e.rel
              }
          )
        | unique_by(.from + "->" + .to)
      )
    }
' "$OUT/resources.json" > "$OUT/graph.json"

# 2.5) Enrich with logical dependencies from codebase + generate resource role table.
if [[ -n "$CODEBASE_ROOT" ]]; then
  python3 - "$OUT/graph.json" "$CODEBASE_ROOT" "$OUT/logical-edges.json" "$OUT/resource-roles.tsv" "$OUT/resource-roles.md" <<'PY'
import json
import pathlib
import re
import sys

graph_path = pathlib.Path(sys.argv[1])
codebase_root = pathlib.Path(sys.argv[2])
logical_edges_path = pathlib.Path(sys.argv[3])
roles_tsv_path = pathlib.Path(sys.argv[4])
roles_md_path = pathlib.Path(sys.argv[5])

graph = json.loads(graph_path.read_text(encoding="utf-8"))
nodes = graph.get("nodes", [])

def lc(s):
  return (s or "").lower()

def find_nodes(type_lc=None, name_contains=None):
  out = []
  for n in nodes:
    t = lc(n.get("type"))
    nm = lc(n.get("label", "").split("\n")[0])
    if type_lc and t != type_lc:
      continue
    if name_contains and name_contains not in nm:
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

logical_edges = []

# Heuristic 1: AI Agent Container App -> Azure OpenAI account
ai_agent_uses_openai = grep_any(
  [
    codebase_root / "apps/services/ai-agent/README.md",
    codebase_root / "cicd/scripts/deploy/deploy-ai-agent.sh",
  ],
  [r"AZURE_OPENAI_ENDPOINT", r"openai\\.azure\\.com", r"openAiEndpoint"],
)
if ai_agent_uses_openai:
  ai_apps = find_nodes("microsoft.app/containerapps", "ai-agent")
  openai_accounts = find_nodes("microsoft.cognitiveservices/accounts", None)
  for src in ai_apps:
    for dst in openai_accounts:
      logical_edges.append(
        {
          "from": src.get("id"),
          "to": dst.get("id"),
          "rel": "openaiApi",
          "source": "codebase",
        }
      )

# Heuristic 2: VOICEVOX Wrapper Container App -> VOICEVOX Engine Container App
wrapper_uses_engine = grep_any(
  [
    codebase_root / "apps/services/voicevox/README.md",
    codebase_root / "cicd/scripts/deploy/deploy-voicevox-wrapper.sh",
  ],
  [r"VOICEVOX_ENGINE_BASE_URL", r"voicevox"],
)
if wrapper_uses_engine:
  wrappers = find_nodes("microsoft.app/containerapps", "vv-wrap")
  engines = [
    n
    for n in find_nodes("microsoft.app/containerapps", "voicevox")
    if "vv-wrap" not in lc(n.get("label", ""))
  ]
  for src in wrappers:
    for dst in engines:
      logical_edges.append(
        {
          "from": src.get("id"),
          "to": dst.get("id"),
          "rel": "voicevoxEngine",
          "source": "codebase",
        }
      )

# Heuristic 3: Function App (BFF) -> VOICEVOX Wrapper API
bff_uses_voicevox = grep_any(
  [
    codebase_root / "apps/bff/src/config.ts",
    codebase_root / "apps/bff/src/clients/voicevoxClient.ts",
  ],
  [r"VOICEVOX_BASE_URL", r"/synthesize", r"voicevox"],
)
if bff_uses_voicevox:
  function_apps = find_nodes("microsoft.web/sites", "func-")
  wrappers = find_nodes("microsoft.app/containerapps", "vv-wrap")
  for src in function_apps:
    for dst in wrappers:
      logical_edges.append(
        {
          "from": src.get("id"),
          "to": dst.get("id"),
          "rel": "voicevoxApi",
          "source": "codebase",
        }
      )

# Keep only edges that point to existing nodes.
node_ids = {n.get("id") for n in nodes}
logical_edges = [
  e
  for e in logical_edges
  if e.get("from") in node_ids and e.get("to") in node_ids and e.get("from") and e.get("to")
]

# Deduplicate by from/to so we don't clash with structural edges.
unique = {}
for e in logical_edges:
  key = (e["from"], e["to"])
  if key not in unique:
    unique[key] = e
logical_edges = list(unique.values())

logical_edges_path.write_text(json.dumps(logical_edges, ensure_ascii=False, indent=2), encoding="utf-8")

ROLE_MAP = {
  "microsoft.web/staticsites": ("Frontend hosting", "Browser UI delivery and edge auth entry point"),
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
  "microsoft.cognitiveservices/accounts": ("LLM endpoint", "Azure OpenAI account for model inference"),
  "microsoft.cognitiveservices/accounts/deployments": ("Model deployment", "Specific model/runtime deployment"),
  "microsoft.containerregistry/registries": ("Container image registry", "Stores deployable images"),
  "microsoft.app/managedenvironments": ("Container app environment", "Shared runtime/network for Container Apps"),
  "microsoft.app/containerapps": ("App microservice", "Runtime endpoint for service workloads"),
}

rows = []
for n in sorted(nodes, key=lambda x: (lc(x.get("rg")), lc(x.get("type")), lc(x.get("label")))):
  t = lc(n.get("type"))
  role, note = ROLE_MAP.get(t, ("General Azure resource", "Role not yet classified"))
  name = (n.get("label", "").split("\n")[0] if n.get("label") else "")
  rows.append(
    {
      "name": name,
      "type": n.get("type", ""),
      "resourceGroup": n.get("rg", ""),
      "role": role,
      "note": note,
      "id": n.get("id", ""),
    }
  )

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
PY

  jq -s '
  .[0] as $g
  | .[1] as $logical
  | ($g.nodes | map(.id) | map({key: ., value: true}) | from_entries) as $ids
  | $g
  | .edges = (
    ($g.edges + ($logical | map(select($ids[.from] and $ids[.to]))))
    | unique_by(.from + "->" + .to)
    )
  ' "$OUT/graph.json" "$OUT/logical-edges.json" > "$OUT/graph.with-code.json"
  mv "$OUT/graph.with-code.json" "$OUT/graph.json"
else
  printf '[]\n' > "$OUT/logical-edges.json"
fi

# 3) Render to DOT (clusters by resource group)
# This is readable, and scalable: clusters reduce clutter.
GENERATED_AT_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

AVAILABLE_ICONS_JSON='[]'
if [[ -n "$ICONS_DIR" ]]; then
  # Build a JSON array of existing PNG basenames so jq can safely fallback when a mapped icon is missing.
  AVAILABLE_ICONS_JSON="$(find "$ICONS_DIR" -maxdepth 1 -type f -name '*.png' -printf '%f\n' | jq -R -s -c 'split("\n") | map(select(length > 0))')"
fi

jq -r --arg iconsDir "$ICONS_DIR" --arg generatedAt "$GENERATED_AT_UTC" --arg subs "$SUBS" --arg rgs "$RGS" --argjson availableIcons "$AVAILABLE_ICONS_JSON" '
  def esc: gsub("\\\\";"\\\\\\\\") | gsub("\"";"\\\\\"");
  def htmlesc:
    gsub("&";"&amp;")
    | gsub("<";"&lt;")
    | gsub(">";"&gt;")
    | gsub("\"";"&quot;");

  def type_short($t):
    ($t | split("/") as $p
     | if ($p|length) >= 2 then ($p[-2] + "/" + $p[-1]) else $t end);

  # Minimal mapping. Provide your own icons in --icons dir with these filenames.
  def iconMap: {
    "microsoft.app/containerapps": "container-apps.png",
    "microsoft.app/managedenvironments": "container-apps-environment.png",
    "microsoft.keyvault/vaults": "keyvault.png",
    "microsoft.network/privateendpoints": "private-endpoint.png",
    "microsoft.network/virtualnetworks": "vnet.png",
    "microsoft.network/privatednszones": "private-dns.png",
    "microsoft.network/privatednszones/virtualnetworklinks": "vnet-link.png",
    "microsoft.operationalinsights/workspaces": "log-analytics.png",
    "microsoft.sql/servers": "sql-server.png",
    "microsoft.sql/servers/databases": "sql-db.png",
    "microsoft.storage/storageaccounts": "storage-account.png",
    "microsoft.web/serverfarms": "app-service-plan.png",
    "microsoft.web/sites": "function-app.png",
    "microsoft.web/staticsites": "static-web-app.png"
  };

  def icon_for($t):
    (iconMap[($t|ascii_downcase)] // null) as $icon
    | if ($icon != null and (($availableIcons | index($icon)) != null)) then $icon else null end;

  def edgeStyle: {
    "privatelink": { color: "#0078D4", style: "solid", label: "Private Link" },
    "linkedbackend": { color: "#8E44AD", style: "dashed", label: "Linked Backend" },
    "serverfarm": { color: "#2E7D32", style: "solid", label: "App Service Plan" },
    "virtualnetwork": { color: "#006064", style: "dotted", label: "VNet Link" },
    "managedenvironment": { color: "#F57C00", style: "solid", label: "Managed Env" },
    "server": { color: "#C62828", style: "solid", label: "SQL Parent" },
    "openaiapi": { color: "#1565C0", style: "dashed", label: "OpenAI API" },
    "voicevoxengine": { color: "#2E7D32", style: "dashed", label: "VOICEVOX Engine" },
    "voicevoxapi": { color: "#6A1B9A", style: "dashed", label: "VOICEVOX API" }
  };

  def edge_stmt($e):
    ((edgeStyle[($e.rel|ascii_downcase)] // { color: "#6B7280", style: "solid", label: $e.rel })) as $st
    | "  \"" + ($e.from|esc) + "\" -> \"" + ($e.to|esc)
      + "\" [color=\"" + ($st.color|tostring|esc)
      + "\", fontcolor=\"" + ($st.color|tostring|esc)
      + "\", style=\"" + ($st.style|tostring|esc)
      + "\", penwidth=1.2, xlabel=\"" + ($st.label|tostring|esc)
      + "\"];";

  def node_stmt($n):
    (icon_for($n.type)) as $icon
    | if ($iconsDir != "" and ($icon|type) == "string" and ($icon|length) > 0) then
        "    \"" + ($n.id|esc) + "\" [shape=plain, label=<" +
          "<TABLE BORDER=\"1\" CELLBORDER=\"0\" CELLPADDING=\"3\" COLOR=\"#D0E6F9\" BGCOLOR=\"#F7FBFF\">" +
            "<TR><TD><IMG SRC=\"" + ($iconsDir|htmlesc) + "/" + ($icon|htmlesc) + "\" SCALE=\"TRUE\"/></TD></TR>" +
            "<TR><TD><FONT POINT-SIZE=\"10\" FACE=\"Segoe UI\"><B>" + (($n.label|split("\n")[0])|tostring|htmlesc) + "</B></FONT></TD></TR>" +
            "<TR><TD><FONT POINT-SIZE=\"8\" FACE=\"Segoe UI\" COLOR=\"#4B5563\">" + (type_short($n.type)|tostring|htmlesc) + "</FONT></TD></TR>" +
          "</TABLE>>];"
      else
        "    \"" + ($n.id|esc) + "\" [shape=plain, label=<" +
          "<TABLE BORDER=\"1\" CELLBORDER=\"0\" CELLPADDING=\"3\" COLOR=\"#D0E6F9\" BGCOLOR=\"#F7FBFF\">" +
            "<TR><TD><FONT POINT-SIZE=\"10\" FACE=\"Segoe UI\"><B>" + (($n.label|split("\n")[0])|tostring|htmlesc) + "</B></FONT></TD></TR>" +
            "<TR><TD><FONT POINT-SIZE=\"8\" FACE=\"Segoe UI\" COLOR=\"#4B5563\">" + (type_short($n.type)|tostring|htmlesc) + "</FONT></TD></TR>" +
          "</TABLE>>];"
      end;

  def name_from_id($id):
    ($id | split("/") | .[-1]);

  def cluster_rg($rgNodes):
    ($rgNodes[0].rg // "") as $rg
    | "  subgraph \"cluster_rg_" + ($rg|tostring|esc) + "\" {",
      "    label=\"RG: " + ($rg|tostring|esc) + "\";",
      "    style=\"rounded,filled\";",
      "    fillcolor=\"#FAFCFF\";",
      "    color=\"#B9D7F5\";",
      "    penwidth=1.2;",
      "",
      (
        ($rgNodes | sort_by(.vnet // "") | group_by(.vnet // ""))
        | .[] as $vgrp
        | ($vgrp[0].vnet // "") as $vnetId
        | if ($vnetId == "") then
            ($vgrp[] | node_stmt(.))
          else
            (
              ( ($rgNodes | map(select(.id == $vnetId)) | .[0].label | split("\\n")[0]) // name_from_id($vnetId) ) as $vnetName
              | "    subgraph \"cluster_vnet_" + ($vnetId|tostring|esc) + "\" {",
                "      label=\"VNet: " + ($vnetName|tostring|esc) + "\";",
                "      style=\"rounded,filled\";",
                "      fillcolor=\"#EEF6FF\";",
                "      color=\"#8ABBE8\";",
                "      penwidth=1.1;",
                "",
                # vnet node itself (if present)
                ($vgrp | map(select(.id == $vnetId))[]? | node_stmt(.)),
                "",
                # subnet clusters
                (
                  ($vgrp | map(select(.subnet != null and .subnet != "" and .id != $vnetId)) | sort_by(.subnet) | group_by(.subnet))
                  | .[] as $sgrp
                  | ($sgrp[0].subnet) as $sid
                  | "      subgraph \"cluster_subnet_" + ($sid|tostring|esc) + "\" {",
                    "        label=\"Subnet: " + (name_from_id($sid)|tostring|esc) + "\";",
                    "        style=\"rounded,filled\";",
                    "        fillcolor=\"#F2FBF5\";",
                    "        color=\"#9ED8B5\";",
                    "        penwidth=1.0;",
                    ($sgrp[] | node_stmt(.)),
                    "      }",
                    ""
                ),
                # nodes in vnet but not in a subnet
                ($vgrp | map(select((.subnet == null or .subnet == "") and .id != $vnetId))[]? | node_stmt(.)),
                "    }",
                ""
            )
          end
      ),
      "  }",
      ""
    ;

  def legend_cluster:
    "  subgraph \"cluster_legend\" {",
    "    label=\"Legend\";",
    "    style=\"rounded,filled\";",
    "    fillcolor=\"#FFFFFF\";",
    "    color=\"#D1D5DB\";",
    "    fontsize=10;",
    "    fontname=\"Segoe UI\";",
    "    legend1 [shape=box, label=\"Resource\", style=\"rounded,filled\", fillcolor=\"#F7FBFF\", color=\"#93C5FD\"];",
    "    legend2 [shape=box, label=\"Resource Group\", style=\"rounded,filled\", fillcolor=\"#FAFCFF\", color=\"#B9D7F5\"];",
    "    legend3 [shape=box, label=\"VNet/Subnet\", style=\"rounded,filled\", fillcolor=\"#EEF6FF\", color=\"#8ABBE8\"];",
    "    legend1 -> legend2 [xlabel=\"Dependency\", color=\"#6B7280\", fontcolor=\"#6B7280\"];",
    "    legend2 -> legend3 [xlabel=\"Private Link\", color=\"#0078D4\", fontcolor=\"#0078D4\"];",
    "  }",
    "";

  "digraph G {",
  "  graph [rankdir=LR, fontsize=10, fontname=\"Segoe UI\", bgcolor=\"white\", pad=0.25, nodesep=0.4, ranksep=0.85, splines=ortho, overlap=false, concentrate=true];",
  "  node  [shape=box, fontsize=10, fontname=\"Segoe UI\", style=rounded, margin=\"0.14,0.08\"];",
  "  edge  [fontsize=8, fontname=\"Segoe UI\", arrowsize=0.7, color=\"#6B7280\"];",
  "  label=\"Mind Inbox Azure Infra Arch\\nGenerated(UTC): " + ($generatedAt|esc) + "\\nScope: subs=" + ($subs|esc) + " | rgs=" + ($rgs|esc) + "\";",
  "  labelloc=\"t\";",
  "  labeljust=\"l\";",
  "",
  # clusters: RG > VNet > Subnet
  (.nodes | sort_by(.rg // "") | group_by(.rg // "")[] ) as $rggrp
  | cluster_rg($rggrp),
  legend_cluster,
  # edges
  (.edges[]? | edge_stmt(.)),
  "}"
' "$OUT/graph.json" > "$OUT/$ARCH_FILE_NAME.dot"

# 4) DOT -> SVG
dot -Tsvg "$OUT/$ARCH_FILE_NAME.dot" > "$OUT/$ARCH_FILE_NAME.svg"

# Some SVG viewers (including VS Code preview) do not load external <image xlink:href="/abs/path.png">.
# If icons are enabled and python3 is available, inline PNGs as data: URIs for portability.
if [[ -n "$ICONS_DIR" ]] && command -v python3 >/dev/null 2>&1; then
  python3 - "$OUT/$ARCH_FILE_NAME.svg" "$ICONS_DIR" <<'PY'
import base64
import os
import re
import sys

svg_path = sys.argv[1]
icons_dir = os.path.abspath(sys.argv[2])

with open(svg_path, "r", encoding="utf-8", errors="replace") as f:
  svg = f.read()

# Match both xlink:href and href (some Graphviz builds vary)
href_re = re.compile(r'(xlink:href|href)="([^"]+)"')

def to_data_uri(path: str):
  # Only inline files under icons_dir
  abs_path = os.path.abspath(path)
  if not abs_path.startswith(icons_dir + os.sep):
    return None
  if not os.path.isfile(abs_path):
    return None
  with open(abs_path, "rb") as img:
    b64 = base64.b64encode(img.read()).decode("ascii")
  return f"data:image/png;base64,{b64}"

def repl(m: re.Match) -> str:
  attr = m.group(1)
  url = m.group(2)
  if url.startswith("data:"):
    return m.group(0)
  # Graphviz emits absolute paths when IMG SRC was absolute.
  # Inline only PNGs in the icons_dir.
  if url.lower().endswith(".png") and os.path.isabs(url):
    data = to_data_uri(url)
    if data:
      return f'{attr}="{data}"'
  return m.group(0)

new_svg = href_re.sub(repl, svg)
if new_svg != svg:
  with open(svg_path, "w", encoding="utf-8") as f:
    f.write(new_svg)
PY
fi

# 5) Mask subscription IDs across generated artifacts (raw GUID + HTML-encoded form
#    used by Graphviz inside <title> elements, where "-" becomes "&#45;").
#    Public docs must not leak the customer's subscription id.
mask_sub_ids() {
  local f sid encoded
  for f in "$@"; do
    [[ -f "$f" ]] || continue
    for sid in "${SUB_IDS[@]}"; do
      [[ -z "$sid" ]] && continue
      encoded="${sid//-/&#45;}"
      sed -i \
        -e "s|${sid}|<SUBSCRIPTION_ID>|g" \
        -e "s|${encoded}|<SUBSCRIPTION_ID>|g" \
        "$f"
    done
  done
}

mask_sub_ids \
  "$OUT/$ARCH_FILE_NAME.svg" \
  "$OUT/$ARCH_FILE_NAME.dot" \
  "$OUT/graph.json" \
  "$OUT/logical-edges.json" \
  "$OUT/resource-roles.tsv" \
  "$OUT/resource-roles.md"

# 6) Copy SVG to docs (for easy reference in markdown)
cp "$OUT/$ARCH_FILE_NAME.svg" "$DOCS_DIR/$ARCH_FILE_NAME.svg"
if [[ -f "$OUT/resource-roles.md" ]]; then
  cp "$OUT/resource-roles.md" "$DOCS_DIR/${ARCH_FILE_NAME}_resource_roles.md"
fi

echo "OK:"
echo "  $OUT/$ARCH_FILE_NAME.svg"
echo "  $OUT/$ARCH_FILE_NAME.dot"
echo "  $OUT/graph.json"
echo "  $OUT/logical-edges.json"
echo "  $OUT/resource-roles.tsv"
echo "  $OUT/resource-roles.md"
echo "  $DOCS_DIR/$ARCH_FILE_NAME.svg"
if [[ -f "$DOCS_DIR/${ARCH_FILE_NAME}_resource_roles.md" ]]; then
  echo "  $DOCS_DIR/${ARCH_FILE_NAME}_resource_roles.md"
fi