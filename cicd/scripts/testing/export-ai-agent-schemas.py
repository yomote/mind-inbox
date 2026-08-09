"""ai-agent の pydantic schema を JSON Schema として標準出力に書き出す。

L0 contract test (`apps/bff/scripts/contract-check.mjs`) から呼ばれる。
ここで test しないこと:
- フィールドの値の妥当性 (それは L2 endpoint test の領域)
- BFF zod 側との比較ロジック (それは contract-check.mjs 側で行う)

このスクリプトは pure な「pydantic → JSON」変換のみを行い、
比較や正規化は呼び出し側 (Node) に委譲する (責務の局所化)。
唯一の例外が `default_factory` の書き出し (下記) — これは pydantic 固有の表現差なので、
比較側に持ち込まずここで潰す。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic.json_schema import GenerateJsonSchema, NoDefault

# `apps/services/ai-agent` を import path に追加
REPO_ROOT = Path(__file__).resolve().parents[3]
AI_AGENT_DIR = REPO_ROOT / "apps" / "services" / "ai-agent"
sys.path.insert(0, str(AI_AGENT_DIR))

from app.schemas import (  # noqa: E402  isort:skip
    ApproveRequest,
    ApproveResponse,
    ChatRequest,
    ChatResponse,
    ChatStreamDelta,
    ChatStreamDone,
    ChatStreamError,
    ExtractionResult,
    ExtractRequest,
    OrganizeRequest,
    OrganizeResponse,
    PlanRequest,
    PlanResponse,
)


class WireSchemaGenerator(GenerateJsonSchema):
    """`default_factory` も `default` として JSON Schema に書き出す。

    契約比較側は「required でない かつ default も無い」= wire から欠落しうる、と判定する。
    pydantic は `Field(default_factory=list)` の default を (副作用を避けるため) JSON Schema に
    出さないので、そのままだと `existing_problems` のような「常に埋まるフィールド」が
    「欠落しうる」と誤判定され、false positive を生む。
    """

    def get_default_value(self, schema):  # type: ignore[no-untyped-def]
        default = super().get_default_value(schema)
        if default is not NoDefault:
            return default
        factory = schema.get("default_factory")
        return factory() if factory is not None else NoDefault


# BFF 側の zod schema 名と対応付ける key を採用する
# (BFF: apps/bff/src/clients/aiAgentContracts.ts / apps/bff/src/trpc/domain.ts)
SCHEMAS = {
    "ChatRequest": ChatRequest,
    "ChatResponse": ChatResponse,
    "ChatStreamDelta": ChatStreamDelta,
    "ChatStreamDone": ChatStreamDone,
    "ChatStreamError": ChatStreamError,
    "OrganizeRequest": OrganizeRequest,
    "OrganizeResponse": OrganizeResponse,
    "PlanRequest": PlanRequest,
    "PlanResponse": PlanResponse,
    "ApproveRequest": ApproveRequest,
    "ApproveResponse": ApproveResponse,
    "ExtractRequest": ExtractRequest,
    "ExtractionResult": ExtractionResult,
}


def main() -> None:
    out = {
        name: model.model_json_schema(schema_generator=WireSchemaGenerator)
        for name, model in SCHEMAS.items()
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
