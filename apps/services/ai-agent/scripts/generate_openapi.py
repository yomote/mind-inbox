"""FastAPI アプリ (`app.main:app`) → `docs/api/ai-agent.yaml` を**派生生成**する (#9 / #82)。

真実は `app/main.py` の route と `app/schemas.py` の pydantic。このスクリプトは
それを FastAPI の `app.openapi()` で introspection して YAML に落とすだけで、
**手書きの記述を一切足さない** (足すと「YAML にはあるが実装には無い」が生まれる)。

生成物は commit し、CI が再生成して `git diff --exit-code` で乖離を赤にする
(BFF の `docs/api/bff-trpc.yaml` と同じ流儀 / docs/api/README.md)。

決定性のための約束:
- `sort_keys=True` で YAML のキー順を固定する。dict の挿入順に依存すると、
  pydantic / FastAPI のバージョン差で **中身が同じなのに diff が出る**。
- `app.version` は `app/main.py` の値をそのまま使う (ここで別途採番しない)。
- `allow_unicode=True` — 日本語の description を `\\uXXXX` に潰さない。

使い方: `npm run docs:openapi:ai-agent` (= uv run python scripts/generate_openapi.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[2]
OUT_PATH = REPO_ROOT / "docs" / "api" / "ai-agent.yaml"

sys.path.insert(0, str(SERVICE_DIR))

from app.main import app  # noqa: E402  (sys.path を通した後でしか import できない)

HEADER = (
    "# 自動生成 — 手書き編集禁止。\n"
    "# 真実は apps/services/ai-agent の FastAPI route + pydantic schema。\n"
    "# 再生成: npm run docs:openapi:ai-agent\n"
)


def main() -> None:
    spec = app.openapi()
    body = yaml.safe_dump(
        spec,
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(HEADER + body, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
