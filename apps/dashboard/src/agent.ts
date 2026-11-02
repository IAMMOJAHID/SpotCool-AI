// Posts to Person A's agent (`AGENT_CHAT_PATH` and `AGENT_VOICE_PATH` in the contracts). The agent
// must allow CORS from the dashboard's origin, `CLIENT_HEADER` included.
import type { ComplaintText } from './wire';

/**
 * Sent with every POST. A custom header makes the browser ask the agent first (a CORS
 * preflight), so no text or audio leaves unless the agent allows this page; a multipart POST
 * alone would be sent, and only its answer hidden.
 */
export const CLIENT_HEADER: readonly [string, string] = ['X-SpotCool-Client', 'dashboard'];

const TIMEOUT_MS = 15_000;
/** `ComplaintText.text` limit. */
export const MAX_COMPLAINT_CHARS = 280;
/** The voice `meta` text: logging only, the agent transcribes the audio itself. */
export const VOICE_META_TEXT = 'Voice complaint recorded on the dashboard';

export async function sendChat(agentUrl: string, text: string): Promise<void> {
  const body: ComplaintText = { text, source: 'occupant' };
  await post(`${agentUrl}/chat`, {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function sendVoice(agentUrl: string, wav: Blob): Promise<void> {
  const meta: ComplaintText = { text: VOICE_META_TEXT, source: 'occupant' };
  const form = new FormData();
  form.append('audio', wav, 'dashboard-voice.wav');
  form.append('meta', JSON.stringify(meta));
  await post(`${agentUrl}/voice`, { body: form });
}

async function post(url: string, init: RequestInit): Promise<void> {
  const headers = new Headers(init.headers);
  headers.set(...CLIENT_HEADER);
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers,
      method: 'POST',
      cache: 'no-store',
      credentials: 'omit',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'TimeoutError') {
      throw new Error(`the agent did not answer within ${TIMEOUT_MS / 1000} s`, { cause: error });
    }
    throw new Error('could not reach the agent (is it running, and does it allow CORS from this page?)', {
      cause: error,
    });
  }
  if (!response.ok) throw new Error(`the agent answered HTTP ${response.status}`);
}
