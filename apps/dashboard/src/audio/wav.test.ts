import { describe, expect, it } from 'vitest';
import { encodeWav, mixToMono, VOICE_RATE_HZ } from './wav';

const ascii = (view: DataView, at: number, n: number) =>
  String.fromCharCode(...Array.from({ length: n }, (_, i) => view.getUint8(at + i)));

describe('encodeWav', () => {
  it('writes a 16 kHz mono 16-bit PCM header (VOICE_FORMAT)', () => {
    const view = new DataView(encodeWav(new Float32Array(100), VOICE_RATE_HZ));
    expect(view.byteLength).toBe(44 + 200);
    expect(ascii(view, 0, 4)).toBe('RIFF');
    expect(view.getUint32(4, true)).toBe(36 + 200);
    expect(ascii(view, 8, 4)).toBe('WAVE');
    expect(ascii(view, 12, 4)).toBe('fmt ');
    expect(view.getUint32(16, true)).toBe(16);
    expect(view.getUint16(20, true)).toBe(1); // PCM
    expect(view.getUint16(22, true)).toBe(1); // mono
    expect(view.getUint32(24, true)).toBe(16_000);
    expect(view.getUint32(28, true)).toBe(32_000);
    expect(view.getUint16(32, true)).toBe(2);
    expect(view.getUint16(34, true)).toBe(16);
    expect(ascii(view, 36, 4)).toBe('data');
    expect(view.getUint32(40, true)).toBe(200);
  });

  it('writes signed little-endian samples, clipped to the 16-bit range', () => {
    const view = new DataView(encodeWav(Float32Array.from([0, 1, -1, 0.5, -0.5, 2, -3, Number.NaN]), VOICE_RATE_HZ));
    const samples = Array.from({ length: 8 }, (_, i) => view.getInt16(44 + i * 2, true));
    expect(samples).toEqual([0, 32767, -32768, 16384, -16384, 32767, -32768, 0]);
    expect(view.getUint8(46)).toBe(0xff); // 32767 little-endian: ff 7f
    expect(view.getUint8(47)).toBe(0x7f);
  });

  it('writes an empty recording as a header only', () => {
    expect(encodeWav(new Float32Array(0), VOICE_RATE_HZ).byteLength).toBe(44);
  });
});

describe('mixToMono', () => {
  it('averages the channels', () => {
    expect(Array.from(mixToMono([Float32Array.from([1, 0.5]), Float32Array.from([0, -0.5])]))).toEqual([0.5, 0]);
  });

  it('passes one channel through and handles none', () => {
    expect(Array.from(mixToMono([Float32Array.from([0.25])]))).toEqual([0.25]);
    expect(mixToMono([]).length).toBe(0);
  });
});
