#!/bin/bash
#
# SessionStart hook for Claude Code on the web.
#
# Each web session runs in a fresh, ephemeral container with a clean clone of
# the repo, so dependencies are never pre-installed. This script installs them
# for every sub-project so that lint / test / build / service startup work.
#
# Runs synchronously: the session starts only after this completes, which
# guarantees deps are ready before Claude runs anything.
#
set -euo pipefail

# Web (remote) environment only — never touch a developer's local machine.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$ROOT"

echo "[session-start] installing dependencies for mind-inbox..."

# 1. Root tooling (husky, prettier, markdownlint, npm-run-all2).
#    Use `install` (not `ci`) so the cached container layer can be reused.
echo "[session-start] root: npm install"
npm install

# 2. BFF (Azure Functions v4 + tRPC).
echo "[session-start] bff: npm install"
npm --prefix apps/bff install

# 3. Frontend (React + Vite, managed with pnpm). Use the pnpm already on PATH
#    (no corepack download). pnpm 10+ only runs build scripts for packages in
#    the `pnpm.onlyBuiltDependencies` allowlist in apps/frontend/package.json.
echo "[session-start] frontend: pnpm install"
pnpm --dir apps/frontend install

# 4. AI Agent (FastAPI + Semantic Kernel, managed with uv). Dev deps include
#    pytest + ruff so tests and linting work.
echo "[session-start] ai-agent: uv sync --dev"
(cd apps/services/ai-agent && uv sync --dev)

# 5. VOICEVOX wrapper (FastAPI, pip/requirements.txt, needs Python 3.12).
echo "[session-start] voicevox: uv venv + pip install"
uv venv --python 3.12 apps/services/voicevox/.venv
uv pip install --python apps/services/voicevox/.venv/bin/python \
  -r apps/services/voicevox/requirements.txt

# 6. Azure Functions Core Tools — required to actually run the BFF host
#    (`func start` / `npm run dev`). Non-fatal if the global install fails.
if ! command -v func >/dev/null 2>&1; then
  echo "[session-start] installing azure-functions-core-tools@4"
  npm install -g azure-functions-core-tools@4 --unsafe-perm true || \
    echo "[session-start] WARN: azure-functions-core-tools install failed (BFF host won't run, build/test still work)"
fi

# Non-secret wiring so the full stack (frontend -> BFF -> ai-agent) talks to
# locally-started services. Secrets (API keys) must NOT be written here — set
# them as secrets in the web environment instead; see docs/dev/web-session-setup.md.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo 'export AI_AGENT_BASE_URL=http://localhost:8000'
    echo 'export VOICEVOX_BASE_URL=http://localhost:8001'
  } >> "$CLAUDE_ENV_FILE"
fi

echo "[session-start] done."
