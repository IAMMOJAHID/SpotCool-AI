// Shared by the end-to-end tests: page URLs, the fixture, a stand-in agent and page checks.
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import type { IncomingHttpHeaders } from 'node:http';
import type { AddressInfo } from 'node:net';
import { expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { MOCK } from '../playwright.config.ts';

export const EVENTS = `ws://${MOCK}/ws/events`;

/** The dashboard against the mock feed; `agent` is a base URL, or none. */
export function dashboard(mode: 'operator' | 'video', agent?: string): string {
  return `/?world=${MOCK}&cameras=${MOCK}${agent ? `&agent=${agent}` : ''}${mode === 'video' ? '&mode=video' : ''}`;
}

export interface Envelope {
  v: 1;
  type: string;
  source: 'world' | 'agent';
  t_ms: number;
  sim_time_s: number | null;
  data: Record<string, unknown>;
}

/** The committed fixture's envelopes, in order. */
export function fixture(): Envelope[] {
  return readFileSync(new URL('../fixtures/sample-events.jsonl', import.meta.url), 'utf8')
    .trim()
    .split('\n')
    .map((line) => (JSON.parse(line) as { envelope: Envelope }).envelope);
}

/** Wait for the fixture's end: the run finished, final KPIs, camera frames and louvers settled. */
export async function playedThrough(page: Page): Promise<void> {
  await expect(page.getByTestId('run-status')).toHaveText('Finished', { timeout: 20_000 });
  await expect(page.getByTestId('energy-saved')).not.toHaveText('—');
  await expect
    .poll(() => page.locator('.tile img').evaluateAll((imgs) => imgs.every((i) => (i as HTMLImageElement).naturalWidth > 0)))
    .toBe(true);
  await page.waitForTimeout(400); // the VRF louvers ease in over 200 ms
}

/** The numbers a screenshot cannot be trusted to show changed: the fixture's results. */
export async function fixtureResults(page: Page): Promise<void> {
  await expect(page.getByTestId('energy-saved')).toHaveText('20.6 %');
  await expect(page.getByTestId('comfort-spotcool')).toHaveText('1.37');
  await expect(page.getByTestId('comfort-baseline')).toHaveText('0.33');
  await expect(page.getByTestId('clock')).toHaveText('19:00');
  await expect(page.getByTestId('day')).toHaveText('Day 200 · 19 Jul');
  await expect(page.getByTestId('run-status')).toHaveText('Finished');
}

/**
 * What in the page is cut off: sticks out of its panel, or out of anything inside the panel that
 * clips it. Text cut short on purpose (an ellipsis or a line clamp) counts as fitting.
 */
export function overflowing(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const found: string[] = [];
    const outside = (r: DOMRect, box: DOMRect) =>
      r.left < box.left - 1 || r.right > box.right + 1 || r.top < box.top - 1 || r.bottom > box.bottom + 1;
    for (const panel of document.querySelectorAll('.panel, .top')) {
      for (const el of panel.querySelectorAll('*')) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        // The nearest clipping ancestor up to the panel, unless it ellipsizes or clamps on purpose.
        for (let a = el.parentElement; a; a = a.parentElement) {
          const s = getComputedStyle(a);
          const clips = s.overflow !== 'visible' || a === panel;
          if (clips && (s.textOverflow === 'ellipsis' || s.webkitLineClamp !== 'none')) break;
          if (clips && outside(r, a.getBoundingClientRect())) {
            const where = panel.getAttribute('data-testid') ?? 'panel';
            const text = el.textContent.trim().slice(0, 40);
            found.push(`${where}: <${el.tagName.toLowerCase()} class="${el.getAttribute('class') ?? ''}"> "${text}"`);
            break;
          }
          if (a === panel) break;
        }
      }
    }
    return found;
  });
}

/** Visible text drawn smaller than `min` px (SVG text at its drawn size), and its size. */
export function smallText(page: Page, min: number, selector = 'body *'): Promise<string[]> {
  return page.evaluate(
    ([min, selector]) => {
      const found: string[] = [];
      for (const el of document.querySelectorAll(selector)) {
        const own = [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? '').trim() !== '');
        if (!own && !el.matches('h2, [data-testid=energy-saved], [data-testid=clock]')) continue;
        const r = el.getBoundingClientRect();
        if (r.width <= 1 || r.height <= 1) continue; // not drawn, or there for screen readers only
        if (getComputedStyle(el).visibility === 'hidden') continue;
        let size = parseFloat(getComputedStyle(el).fontSize);
        if (el instanceof SVGGraphicsElement) {
          const m = el.getScreenCTM();
          if (m) size *= Math.hypot(m.a, m.b);
        }
        if (size < min - 0.05) found.push(`<${el.tagName.toLowerCase()}> "${el.textContent.trim().slice(0, 30)}" ${size.toFixed(1)} px`);
      }
      return found;
    },
    [min, selector] as const,
  );
}

export interface Received {
  method: string;
  path: string;
  headers: IncomingHttpHeaders;
  body: Buffer;
}

/** A stand-in for Person A's agent on a free port; with `cors`, it allows this page's requests. */
export async function agentServer(cors: boolean): Promise<{ url: string; received: Received[]; close: () => Promise<void> }> {
  const received: Received[] = [];
  const server = createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on('data', (chunk: Buffer) => chunks.push(chunk));
    request.on('end', () => {
      received.push({ method: request.method ?? '', path: request.url ?? '', headers: request.headers, body: Buffer.concat(chunks) });
      if (cors) {
        response.setHeader('Access-Control-Allow-Origin', request.headers.origin ?? '*');
        response.setHeader('Access-Control-Allow-Methods', 'POST');
        response.setHeader('Access-Control-Allow-Headers', 'content-type, x-spotcool-client');
      }
      if (request.method === 'OPTIONS') response.writeHead(204).end();
      else response.writeHead(200, { 'Content-Type': 'application/json' }).end('{"ok":true}');
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${port}`,
    received,
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => {
          resolve();
        });
      }),
  };
}

/** The named parts of a multipart body. */
export function multipart(body: Buffer, contentType: string): Map<string, Buffer> {
  const boundary = contentType.match(/boundary=(.+)$/)?.[1] ?? '';
  const parts = new Map<string, Buffer>();
  for (const section of body.toString('latin1').split(`--${boundary}`)) {
    const name = section.match(/name="([^"]+)"/)?.[1];
    if (name) parts.set(name, Buffer.from(section.slice(section.indexOf('\r\n\r\n') + 4, -2), 'latin1'));
  }
  return parts;
}
