"""ai-agent の pydantic schema を JSON Schema として標準出力に書き出す。

L0 contract test (`cicd/scripts/testing/contract-check.mjs`) から呼ばれる。
ここで test しないこと:
- フィールドの値の妥当性 (それは L2 endpoint test の領域)
- BFF zod 側との比較ロジック (それは contract-check.mjs 側で行う)

このスクリプトは pure な「pydantic → JSON」変換のみを行い、
比較や正規化は呼び出し側 (Node) に委譲する (責務の局所化)。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# `apps/services/ai-agent` を import path に追加
REPO_ROOT = Path(__file__).resolve().parents[3]
AI_AGENT_DIR = REPO_ROOT / "apps" / "services" / "ai-agent"
sys.path.insert(0, str(AI_AGENT_DIR))

from app.schemas import (  # noqa: E402  isort:skip
    ApproveRequest,
    OrganizeResponse,
    PlanRequest,
    PlanResponse,
)


# BFF 側の zod schema 名と対応付ける key を採用する
SCHEMAS = {
    "OrganizeResponse": OrganizeResponse,
    "PlanRequest": PlanRequest,
    "PlanResponse": PlanResponse,
    "ApproveRequest": ApproveRequest,
}


def main() -> None:
    out = {name: model.model_json_schema() for name, model in SCHEMAS.items()}
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
