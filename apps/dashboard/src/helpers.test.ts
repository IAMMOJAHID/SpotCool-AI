import { describe, expect, it } from 'vitest';
import { parseCatalog, mjpegUrl } from './cameras';
import { inkFor, tempColor } from './colors';
import { readConfig } from './config';
import { backoffMs } from './feed';
import { bmsValue, calendarDate, clockText, dayOfYear, fixed, plantLabel, scenarioLabel, simSeconds } from './format';
import { initialState } from './state/reducer';
import { dayWindow, doorIntervals, meetings } from './state/selectors';
import { tween } from './tween';

const DAY = 199 * 86_400;

describe('format', () => {
  it('formats sim time as the world does', () => {
    expect(clockText(DAY + 14 * 3600 + 15 * 60)).toBe('14:15');
    expect(clockText(DAY + 86_399.6)).toBe('00:00');
    expect(dayOfYear(DAY + 3600)).toBe(200);
    expect(calendarDate(DAY + 3600)).toBe('19 Jul');
  });

  it('reads calendar date-times back as sim time', () => {
    expect(simSeconds('2026-07-19T14:00:00')).toBe(DAY + 14 * 3600);
    expect(simSeconds('2026-07-19T14:00:00Z')).toBe(DAY + 14 * 3600);
    expect(simSeconds('soon')).toBeNull();
  });

  it('names plants, scenarios and values', () => {
    expect(plantLabel('boptest-local')).toBe('BOPTEST (local)');
    expect(plantLabel('rc')).toBe('RC model');
    expect(plantLabel('other')).toBe('other');
    expect(scenarioLabel('hybrid_tuesday')).toBe('Hybrid Tuesday');
    expect(fixed(null, 1)).toBe('—');
    expect(fixed(16.87, 1)).toBe('16.9');
    expect(bmsValue(null)).toBe('NULL (release)');
    expect(bmsValue(28)).toBe('28');
    expect(bmsValue(26.12345)).toBe('26.123');
    expect(bmsValue('Unoccupied')).toBe('Unoccupied');
  });
});

describe('readConfig', () => {
  const page = { host: '192.168.1.20:8090', protocol: 'http:' };

  it('uses the page host in production and 127.0.0.1:8090 in development', () => {
    const prod = readConfig('', page, { agentUrl: undefined, dev: false });
    expect(prod.eventsUrl).toBe('ws://192.168.1.20:8090/ws/events');
    expect(prod.camerasUrl).toBe('http://192.168.1.20:8081');
    expect(readConfig('', page, { agentUrl: undefined, dev: true }).eventsUrl).toBe('ws://127.0.0.1:8090/ws/events');
  });

  it('takes ?world=, ?cameras=, ?agent= and ?mode=video', () => {
    const c = readConfig('?world=127.0.0.1:18190&cameras=127.0.0.1:18181&agent=http://127.0.0.1:8002/&mode=video', page, { agentUrl: 'http://env:1', dev: false });
    expect(c.eventsUrl).toBe('ws://127.0.0.1:18190/ws/events');
    expect(c.camerasUrl).toBe('http://127.0.0.1:18181');
    expect(c.agentUrl).toBe('http://127.0.0.1:8002');
    expect(c.video).toBe(true);
  });

  it('falls back to VITE_AGENT_URL, and reports a bad agent URL instead of using it', () => {
    expect(readConfig('', page, { agentUrl: 'http://a:8002', dev: false }).agentUrl).toBe('http://a:8002');
    const bad = readConfig('?agent=javascript:alert(1)', page, { agentUrl: undefined, dev: false });
    expect(bad.agentUrl).toBeNull();
    expect(bad.agentProblem).toContain('not an http');
    expect(readConfig('', page, { agentUrl: undefined, dev: false }).agentUrl).toBeNull();
  });

  it('ignores a world that is not host:port', () => {
    expect(readConfig('?world=evil.example/x?y', page, { agentUrl: undefined, dev: false }).world).toBe('192.168.1.20:8090');
  });
});

describe('selectors', () => {
  it('turns door samples into open intervals', () => {
    const samples = [
      { t: 1, open: false },
      { t: 2, open: true },
      { t: 3, open: true },
      { t: 4, open: false },
      { t: 5, open: true },
    ];
    expect(doorIntervals(samples)).toEqual([
      { from: 2, to: 4 },
      { from: 5, to: null },
    ]);
  });

  it('lists bookings, not free slots', () => {
    const slot = (status: 'busy' | 'free', h: number) => ({ status, subject: 'Review', start: { dateTime: `2026-07-19T${h}:00:00` }, end: { dateTime: `2026-07-19T${h + 1}:00:00` } });
    expect(meetings({ 'meeting-south': { items: [slot('free', 10), slot('busy', 14)], t: 0 } })).toEqual([
      { room: 'meeting-south', subject: 'Review', from: DAY + 14 * 3600, to: DAY + 15 * 3600 },
    ]);
  });

  it('spans the default day 07:00-19:00, widened to the data', () => {
    expect(dayWindow(initialState)).toBeNull();
    expect(dayWindow({ ...initialState, simNow: DAY + 9 * 3600 })).toEqual([DAY + 7 * 3600, DAY + 19 * 3600]);
    expect(dayWindow({ ...initialState, simNow: DAY + 20 * 3600 })).toEqual([DAY + 7 * 3600, DAY + 20 * 3600]);
  });
});

describe('cameras', () => {
  it('keeps well-formed cameras only', () => {
    const cam = { id: 'cam-sou', name: 'South', zone: 'sou', snapshot_url: 'http://h:8081/cameras/cam-sou/snapshot.jpg', width: 1280, height: 720, fps_hint: 5, source: 'scenario' };
    const parsed = parseCatalog({ cameras: [cam, { ...cam, id: '../x' }, { ...cam, snapshot_url: 'javascript:1' }], privacy_notice: 'n' });
    expect(parsed?.cameras.map((c) => c.id)).toEqual(['cam-sou']);
    expect(parsed?.privacy_notice).toBe('n');
    expect(parseCatalog({})).toBeNull();
    expect(mjpegUrl('http://h:8081', 'cam-sou')).toBe('http://h:8081/cameras/cam-sou/mjpeg');
  });
});

describe('small pieces', () => {
  it('tweens linearly and lands on the target', () => {
    expect(tween(0, -60, 0, 200)).toBe(0);
    expect(tween(0, -60, 100, 200)).toBe(-30);
    expect(tween(0, -60, 500, 200)).toBe(-60);
    expect(tween(0, -60, -5, 200)).toBe(0);
  });

  it('colours temperatures cool to warm, with readable ink', () => {
    expect(tempColor(10)).toBe(tempColor(22));
    expect(tempColor(35)).toBe('rgb(220, 38, 38)');
    expect(inkFor(tempColor(26))).toBe('#05070a');
    expect(inkFor(tempColor(22))).toBe('#ffffff');
  });

  it('backs off, then repeats the longest wait', () => {
    expect(backoffMs(0)).toBe(500);
    expect(backoffMs(99)).toBe(8000);
  });
});
