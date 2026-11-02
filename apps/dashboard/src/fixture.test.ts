import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { makeFixture } from '../scripts/make-fixture.ts';
import { initialState, reducer } from './state/reducer';
import type { Envelope } from './wire';
import { EVENT_TYPES, isRecord, parseEnvelope } from './wire';

const root = join(import.meta.dirname, '..');
const fixture = readFileSync(join(root, 'fixtures', 'sample-events.jsonl'), 'utf8');

describe('fixtures/sample-events.jsonl', () => {
  const envelopes = fixture
    .trim()
    .split('\n')
    .map((line) => {
      const record: unknown = JSON.parse(line);
      return isRecord(record) && record.kind === 'event' ? parseEnvelope(JSON.stringify(record.envelope)) : null;
    });

  it('is what scripts/make-fixture.ts makes', () => {
    expect(fixture).toBe(`${makeFixture().join('\n')}\n`);
  });

  it('holds only valid envelopes, covering every event type', () => {
    expect(envelopes.every((e) => e !== null)).toBe(true);
    expect(new Set(envelopes.map((e) => e?.type))).toEqual(new Set(EVENT_TYPES));
  });

  it('covers a Mitsubishi and an LG unit, a VRF write, both complaint outcomes and a final run', () => {
    const state = reducer(initialState, { kind: 'events', envelopes: envelopes as Envelope[], dropped: 0 });
    const profiles = new Set(envelopes.filter((e) => e?.type === 'vrf_state').map((e) => e?.data.profile));
    expect(profiles).toEqual(new Set(['lg', 'mitsubishi']));
    expect(state.bacnet.some((r) => r.data.device === 'VRF-Gateway-MeetingRoom')).toBe(true);
    expect(state.bacnet.some((r) => !r.data.accepted)).toBe(true);
    expect(state.complaints.map((c) => c.status)).toEqual(['delivered', 'failed']);
    expect(state.run?.status).toBe('finished');
    expect(state.kpi.spotcool?.data.provisional).toBe(false);
  });
});

describe('Law 6', () => {
  const sources = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) return entry.name === 'contracts' ? [] : sources(path);
      return /\.(ts|tsx|mjs)$/.test(entry.name) && !entry.name.endsWith('.test.ts') ? [path] : [];
    });

  it('no code keeps anything in browser storage, caches or a service worker', () => {
    const files = [...sources(join(root, 'src')), ...sources(join(root, 'scripts')), join(root, 'index.html')];
    for (const file of files) {
      expect(readFileSync(file, 'utf8'), file).not.toMatch(/localStorage|sessionStorage|indexedDB|caches\.|serviceWorker|CacheStorage/);
    }
  });
});
