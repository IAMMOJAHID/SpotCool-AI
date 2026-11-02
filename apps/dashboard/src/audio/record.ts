// Hold-to-talk recording. Law 6: the audio lives only in memory, from the microphone to the one
// POST to the agent; nothing here stores, caches or logs it.
import { encodeWav, mixToMono, VOICE_RATE_HZ } from './wav';

/** A recording stops itself after this long, so a stuck key cannot record without end. */
export const MAX_RECORDING_MS = 60_000;

export interface Recording {
  /** How long it has been recording. */
  elapsedMs(): number;
  /** Stop and return the audio as a `VOICE_FORMAT` WAV. */
  stop(): Promise<Blob>;
  /** Stop and drop the audio. */
  cancel(): void;
}

export async function startRecording(): Promise<Recording> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const recorder = new MediaRecorder(stream);
  const chunks: Blob[] = [];
  recorder.ondataavailable = (event) => {
    chunks.push(event.data);
  };
  const stopped = new Promise<void>((resolve) => {
    recorder.onstop = () => {
      resolve();
    };
  });
  const end = () => {
    window.clearTimeout(limit);
    if (recorder.state !== 'inactive') recorder.stop();
    for (const track of stream.getTracks()) track.stop();
  };
  const limit = window.setTimeout(end, MAX_RECORDING_MS);
  recorder.start();
  const started = performance.now();
  return {
    elapsedMs: () => performance.now() - started,
    async stop() {
      end();
      await stopped;
      const encoded = await new Blob(chunks).arrayBuffer();
      chunks.length = 0;
      return new Blob([await toVoiceFormat(encoded)], { type: 'audio/wav' });
    },
    cancel() {
      end();
      chunks.length = 0;
    },
  };
}

/** Decode with Web Audio, which resamples to its context's 16 kHz, mix to mono, encode as WAV. */
async function toVoiceFormat(encoded: ArrayBuffer): Promise<ArrayBuffer> {
  const decoded = await new OfflineAudioContext(1, 1, VOICE_RATE_HZ).decodeAudioData(encoded);
  const channels = Array.from({ length: decoded.numberOfChannels }, (_, c) => decoded.getChannelData(c));
  return encodeWav(mixToMono(channels), VOICE_RATE_HZ);
}
