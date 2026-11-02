import { useEffect } from 'react';
import type { Action } from './state/reducer';
import type { Envelope } from './wire';
import { parseEnvelope } from './wire';

/** Waits before each reconnect; the last one repeats. */
export const BACKOFF_MS = [500, 1000, 2000, 4000, 8000];
/** Messages arriving within this long are folded into the state in one render. */
const BATCH_MS = 16;

export function backoffMs(attempt: number): number {
  return BACKOFF_MS[Math.min(Math.max(attempt, 0), BACKOFF_MS.length - 1)] ?? 8000;
}

/** Reads WP6's read-only `/ws/events` into `dispatch`, reconnecting with backoff. */
export function useEventFeed(url: string, dispatch: (action: Action) => void): void {
  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let flush: number | undefined;
    let attempt = 0;
    let closed = false;
    let pending: Envelope[] = [];
    let dropped = 0;
    const deliver = () => {
      flush = undefined;
      const envelopes = pending;
      const lost = dropped;
      pending = [];
      dropped = 0;
      dispatch({ kind: 'events', envelopes, dropped: lost });
    };
    const connect = () => {
      const ws = new WebSocket(url);
      socket = ws;
      ws.onopen = () => {
        attempt = 0;
        dispatch({ kind: 'connection', connection: 'live', attempt });
      };
      ws.onmessage = (message: MessageEvent<unknown>) => {
        const envelope = typeof message.data === 'string' ? parseEnvelope(message.data) : null;
        if (envelope) pending.push(envelope);
        else dropped += 1;
        flush ??= window.setTimeout(deliver, BATCH_MS);
      };
      ws.onclose = () => {
        if (closed) return;
        if (flush !== undefined) {
          window.clearTimeout(flush);
          deliver(); // what came before the close lands before the connection changes
        }
        attempt += 1;
        dispatch({ kind: 'connection', connection: 'reconnecting', attempt });
        retry = window.setTimeout(connect, backoffMs(attempt - 1));
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearTimeout(flush);
      socket?.close();
    };
  }, [url, dispatch]);
}
