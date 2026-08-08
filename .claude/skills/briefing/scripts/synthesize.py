#!/usr/bin/env python3
"""報告会のナレーション音声を VOICEVOX で合成し、data URI 用の base64 を吐く。

使い方:
    python3 synthesize.py scripts.json out_dir/
      scripts.json : {"1": "1 枚目の原稿", "2": "...", ...} (キーはスライド番号)
      out_dir      : 生成物の置き場 (b64.json / sN.mp3 / sN.wav)

前提: `cicd/scripts/local-voicevox/start-voicevox.sh` で engine が起動していること。
出力: out_dir/b64.json = {"1": "<base64 mp3>", ...} → embed_audio.py に渡す。
"""

import base64
import json
import pathlib
import subprocess
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:50021"
SPEAKER = 3  # ずんだもん (ノーマル)
SPEED = 1.5  # briefing #1 で PO が「1.5 倍が聞きやすい」と評価 (skill Step 4)
BITRATE = "40k"


def synthesize(text: str, wav_path: pathlib.Path) -> None:
    query_url = f"{BASE}/audio_query?" + urllib.parse.urlencode(
        {"text": text, "speaker": SPEAKER}
    )
    with urllib.request.urlopen(
        urllib.request.Request(query_url, method="POST"), timeout=120
    ) as res:
        query = json.load(res)
    query["speedScale"] = SPEED

    synth_req = urllib.request.Request(
        f"{BASE}/synthesis?speaker={SPEAKER}",
        data=json.dumps(query).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(synth_req, timeout=300) as res:
        wav_path.write_bytes(res.read())


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    scripts = json.loads(pathlib.Path(sys.argv[1]).read_text())
    out = pathlib.Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)

    encoded: dict[str, str] = {}
    for slide, text in sorted(scripts.items(), key=lambda kv: int(kv[0])):
        wav = out / f"s{slide}.wav"
        mp3 = out / f"s{slide}.mp3"
        synthesize(text, wav)
        # 24kHz mono / 40kbps — 聞き取りに十分で Artifact の 16MB 制限に余裕をもって収まる
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
             "-ac", "1", "-ar", "24000", "-b:a", BITRATE, str(mp3)],
            check=True,
        )
        blob = mp3.read_bytes()
        encoded[slide] = base64.b64encode(blob).decode()
        print(f"slide {slide}: mp3={len(blob) // 1024}KB", flush=True)

    (out / "b64.json").write_text(json.dumps(encoded))
    total = sum(len(v) for v in encoded.values())
    print(f"TOTAL base64 = {total / 1024 / 1024:.1f}MB (Artifact 上限 16MB)")
    if total > 14 * 1024 * 1024:
        print("WARN: 上限に近い。原稿を短くするかスライドを分割すること", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
