#!/usr/bin/env python3
"""報告会のナレーション音声を VOICEVOX で合成し、data URI 用の base64 を吐く。

使い方:
    python3 synthesize.py scripts.json out_dir/
      scripts.json : {"1": "1 枚目の原稿", "2": "...", ...} (キーはスライド番号)
      out_dir      : 生成物の置き場 (b64.json / sN.mp3 / sN.wav)

    python3 synthesize.py scripts.json --check
      engine 無しで読みの正規化結果だけを表示する (辞書の穴を先に潰すため)

前提: `cicd/scripts/local-voicevox/start-voicevox.sh` で engine が起動していること。
出力: out_dir/b64.json = {"1": "<base64 mp3>", ...} → embed_audio.py に渡す。

読み (briefing #6 の PO 指摘):
    VOICEVOX は英字・略語を素直に読まないので、合成前に reading-dict.json で
    カタカナへ正規化する。辞書に無い英字が残ったら**警告して一覧を出す** —
    黙って変な読みのまま出さないため。警告に出た語は辞書へ足すこと。

速度:
    合成はスライド単位で並列 (既定 4 並列)。engine の起動 (docker pull) が
    支配的なので、skill 側で「スライドを書く前に background で起動」を守ること。
"""

import base64
import json
import pathlib
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "http://127.0.0.1:50021"
SPEAKER = 3  # ずんだもん (ノーマル)
SPEED = 1.5  # briefing #1 で PO が「1.5 倍が聞きやすい」と評価 (skill Step 4)
BITRATE = "40k"
WORKERS = 4

DICT_PATH = pathlib.Path(__file__).with_name("reading-dict.json")
DIGITS = {"0": "ゼロ", "1": "イチ", "2": "ニ", "3": "サン", "4": "ヨン",
          "5": "ゴ", "6": "ロク", "7": "ナナ", "8": "ハチ", "9": "キュウ"}


def load_dict() -> list[tuple[str, str]]:
    raw = json.loads(DICT_PATH.read_text())
    items = [(k, v) for k, v in raw.items() if not k.startswith("_")]
    # 長いキーを先に当てる (CI が CI/CD を食い荒らさないように)
    return sorted(items, key=lambda kv: len(kv[0]), reverse=True)


def normalize(text: str, table: list[tuple[str, str]]) -> str:
    """原稿を VOICEVOX が読める形に正規化する。"""
    # ADR 0034 のような 4 桁は桁読みしないと「サンジュウヨン」等に化ける
    text = re.sub(
        r"(ADR|adr)\s*(\d{4})",
        lambda m: "ADR " + "".join(DIGITS[d] for d in m.group(2)),
        text,
    )
    # 単体で出てくる 0021 のような ADR 番号も桁読みにする
    text = re.sub(r"(?<!\d)0(\d{3})(?!\d)",
                  lambda m: "".join(DIGITS[d] for d in "0" + m.group(1)), text)
    text = re.sub(r"#(\d+)", r"\1 番", text)
    for src, dst in table:
        text = text.replace(src, dst)
    # ASCII 空白は VOICEVOX でアクセント句の切れ目になり、「19 本」「ピーアール が」の
    # ような箇所で不自然な間 (詰まった読み) を作る (briefing #8 の PO 指摘)。
    # 日本語文字・数字どうしに挟まれた空白は詰めてから合成する
    jp = "0-9ぁ-ゖァ-ヺー一-鿿々"
    text = re.sub(rf"(?<=[{jp}])[ \t]+(?=[{jp}])", "", text)
    return text


def unknown_latin(text: str) -> list[str]:
    """正規化後も残った英字 = 辞書の穴。"""
    return sorted({m.group(0) for m in re.finditer(r"[A-Za-z][A-Za-z0-9_./-]*", text)})


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


def render(slide: str, text: str, out: pathlib.Path) -> tuple[str, bytes]:
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
    print(f"slide {slide}: mp3={len(blob) // 1024}KB", flush=True)
    return slide, blob


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    scripts = json.loads(pathlib.Path(sys.argv[1]).read_text())
    table = load_dict()
    ordered = sorted(scripts.items(), key=lambda kv: int(kv[0]))
    normalized = {slide: normalize(text, table) for slide, text in ordered}

    holes = {s: unknown_latin(t) for s, t in normalized.items()}
    holes = {s: v for s, v in holes.items() if v}
    if holes:
        print("WARN: 読み辞書に無い英字が残っています (変な読みになります)", file=sys.stderr)
        for slide, words in holes.items():
            print(f"  slide {slide}: {', '.join(words)}", file=sys.stderr)
        print("  → reading-dict.json に足してから合成し直すこと", file=sys.stderr)

    if sys.argv[2] == "--check":
        for slide, text in normalized.items():
            print(f"--- slide {slide} ---\n{text}\n")
        return 1 if holes else 0

    out = pathlib.Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(
            lambda kv: render(kv[0], kv[1], out), normalized.items()
        ))

    encoded = {slide: base64.b64encode(blob).decode() for slide, blob in results}
    (out / "b64.json").write_text(json.dumps(encoded))
    total = sum(len(v) for v in encoded.values())
    print(f"TOTAL base64 = {total / 1024 / 1024:.1f}MB (Artifact 上限 16MB)")
    if total > 14 * 1024 * 1024:
        print("WARN: 上限に近い。原稿を短くするかスライドを分割すること", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
