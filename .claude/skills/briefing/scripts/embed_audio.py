#!/usr/bin/env python3
"""合成済み音声をスライド HTML に埋め込み、進行ロジックが生きているか検証する。

使い方:
    python3 embed_audio.py deck.html audio/b64.json [scripts.json]

deck.html は `deck-template.html` 由来であること (以下のマーカーを持つ):
    const AUDIO = {/*__AUDIO__*/};
    const SCRIPTS = {/*__SCRIPTS__*/};

**なぜマーカー方式か**: briefing #1 で、プレイヤーを正規表現で「置換」した際に
スライド送り関数 `show()` を巻き込み削除し、ページングを壊した。所定の穴に
差し込むだけにすれば、進行ロジックに触れずに済む (構造的な再発防止)。
埋め込み後は必ず JS の構文と必須関数の存在を検証する。
"""

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REQUIRED_FUNCS = ["function show(", "function playNarration(", "function stopAudio("]


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    deck = pathlib.Path(sys.argv[1])
    # 差し込み前の原本を残しておき、2 回目以降はそこから作り直す。
    # 音声を録り直すたびにスライドを書き直していては遅い (briefing #6 の PO 指摘)。
    src = deck.with_suffix(".src.html")
    if src.exists():
        html = src.read_text()
    else:
        html = deck.read_text()
        src.write_text(html)
    b64 = json.loads(pathlib.Path(sys.argv[2]).read_text())

    audio_literal = "{\n" + ",\n".join(
        f'  "{k}": "data:audio/mpeg;base64,{v}"'
        for k, v in sorted(b64.items(), key=lambda kv: int(kv[0]))
    ) + "\n}"
    if "{/*__AUDIO__*/}" not in html:
        print("ERROR: deck に {/*__AUDIO__*/} マーカーが無い (テンプレート由来か確認)", file=sys.stderr)
        return 1
    html = html.replace("{/*__AUDIO__*/}", audio_literal)

    if len(sys.argv) > 3:
        scripts = json.loads(pathlib.Path(sys.argv[3]).read_text())
        scripts_literal = json.dumps(scripts, ensure_ascii=False, indent=1)
        if "{/*__SCRIPTS__*/}" not in html:
            print("ERROR: deck に {/*__SCRIPTS__*/} マーカーが無い", file=sys.stderr)
            return 1
        html = html.replace("{/*__SCRIPTS__*/}", scripts_literal)

    # --- 検証 1: 進行ロジックが生きているか (briefing #1 の再発防止) ---
    missing = [f for f in REQUIRED_FUNCS if f not in html]
    if missing:
        print(f"ERROR: 進行ロジックが欠落: {missing}", file=sys.stderr)
        return 1

    # --- 検証 2: JS 構文 (node があれば) ---
    script = re.search(r"<script>(.*)</script>", html, re.S)
    if script and shutil.which("node"):
        body = re.sub(r'"data:audio/mpeg;base64,[^"]+"', '"X"', script.group(1))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tmp:
            tmp.write(body)
            tmp_path = tmp.name
        result = subprocess.run(["node", "--check", tmp_path], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ERROR: JS 構文エラー\n{result.stderr}", file=sys.stderr)
            return 1
        print("JS 構文 OK")
    elif not shutil.which("node"):
        print("WARN: node が無いため構文チェックをスキップ (ブラウザで手動確認すること)")

    deck.write_text(html)
    size_mb = len(html.encode()) / 1024 / 1024
    print(f"埋め込み完了: {deck} ({size_mb:.1f}MB) / 音声 {len(b64)} 枚")
    if size_mb > 15:
        print("WARN: Artifact の 16MB 上限に近い", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
