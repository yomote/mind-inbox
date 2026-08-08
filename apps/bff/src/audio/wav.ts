/**
 * WAV (RIFF) の最小限のパース / 結合ユーティリティ。
 *
 * 用途: TTS の文単位分割合成 (#120) — VOICEVOX wrapper が返す文ごとの WAV を
 * 1 本の WAV に結合してフロントへ返す。フロントの再生コード (単一 Blob 前提) を
 * 変えずに分割合成の恩恵を得るための変換。
 *
 * 前提: 入力はすべて同一の合成設定 (同一話者 / 同一サンプルレート) で生成された
 * PCM WAV。fmt チャンクがバイト単位で一致しない場合は例外を投げる (無音や
 * ノイズとして静かに壊れるより、呼び出し側で全文一括合成へフォールバックさせる)。
 */

type WavChunks = {
  /** fmt チャンクのペイロード (チャンクヘッダ除く) */
  fmt: Uint8Array;
  /** data チャンクのペイロード (チャンクヘッダ除く) */
  data: Uint8Array;
};

function ascii(view: DataView, offset: number, length: number): string {
  let s = "";
  for (let i = 0; i < length; i++) {
    s += String.fromCharCode(view.getUint8(offset + i));
  }
  return s;
}

/** RIFF/WAVE をチャンク走査し fmt / data を取り出す。壊れていれば例外。 */
export function parseWav(buffer: ArrayBuffer): WavChunks {
  const view = new DataView(buffer);
  if (buffer.byteLength < 12 || ascii(view, 0, 4) !== "RIFF" || ascii(view, 8, 4) !== "WAVE") {
    throw new Error("parseWav: RIFF/WAVE ヘッダではありません");
  }

  let fmt: Uint8Array | null = null;
  let data: Uint8Array | null = null;
  let offset = 12;
  while (offset + 8 <= buffer.byteLength) {
    const id = ascii(view, offset, 4);
    const size = view.getUint32(offset + 4, true);
    const payloadStart = offset + 8;
    const payloadEnd = Math.min(payloadStart + size, buffer.byteLength);
    if (id === "fmt ") {
      fmt = new Uint8Array(buffer, payloadStart, payloadEnd - payloadStart);
    } else if (id === "data") {
      data = new Uint8Array(buffer, payloadStart, payloadEnd - payloadStart);
    }
    // チャンクは 2 バイト境界にパディングされる
    offset = payloadStart + size + (size % 2);
  }

  if (!fmt || !data) {
    throw new Error("parseWav: fmt / data チャンクが見つかりません");
  }
  return { fmt, data };
}

function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

/**
 * 複数の WAV を 1 本に結合する。fmt は先頭のものを採用し、全 WAV の fmt が
 * 一致することを検証する。data 以外の付随チャンク (LIST 等) は落とす。
 */
export function concatWavs(buffers: ArrayBuffer[]): ArrayBuffer {
  if (buffers.length === 0) {
    throw new Error("concatWavs: 入力が空です");
  }
  if (buffers.length === 1) return buffers[0];

  const parsed = buffers.map(parseWav);
  const fmt = parsed[0].fmt;
  for (const p of parsed.slice(1)) {
    if (!bytesEqual(p.fmt, fmt)) {
      throw new Error("concatWavs: fmt チャンクが一致しません (異なる合成設定の WAV)");
    }
  }

  const dataLength = parsed.reduce((sum, p) => sum + p.data.length, 0);
  const fmtChunkSize = 8 + fmt.length + (fmt.length % 2);
  const totalSize = 12 + fmtChunkSize + 8 + dataLength;

  const out = new ArrayBuffer(totalSize);
  const view = new DataView(out);
  const bytes = new Uint8Array(out);

  const writeAscii = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) bytes[offset + i] = s.charCodeAt(i);
  };

  writeAscii(0, "RIFF");
  view.setUint32(4, totalSize - 8, true);
  writeAscii(8, "WAVE");

  let offset = 12;
  writeAscii(offset, "fmt ");
  view.setUint32(offset + 4, fmt.length, true);
  bytes.set(fmt, offset + 8);
  offset += fmtChunkSize;

  writeAscii(offset, "data");
  view.setUint32(offset + 4, dataLength, true);
  offset += 8;
  for (const p of parsed) {
    bytes.set(p.data, offset);
    offset += p.data.length;
  }

  return out;
}
