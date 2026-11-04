import { describe, expect, it } from 'vitest';
import { parseCatalog, mjpegUrl, privacyUrl } from './cameras';
import { inkFor, tempColor } from './colors';
import { isLocalHost, readConfig } from './config';
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

  it('never shows a negative zero', () => {
    expect([fixed(-0.3, 0), fixed(-0.04, 1), fixed(-0, 2)]).toEqual(['0', '0.0', '0.00']);
    expect([fixed(-0.6, 0), fixed(-1.26, 1)]).toEqual(['-1', '-1.3']);
  });
});

describe('readConfig', () => {
  const page = { host: '192.168.1.20:8090', hostname: '192.168.1.20', protocol: 'http:' };
  const prod = { agentUrl: undefined, dev: false };

  it('uses the page host in production and 127.0.0.1:8090 in development', () => {
    const c = readConfig('', page, prod);
    expect(c.eventsUrl).toBe('ws://192.168.1.20:8090/ws/events');
    expect(c.camerasUrl).toBe('http://192.168.1.20:8081');
    expect(c.problems).toEqual([]);
    expect(readConfig('', page, { agentUrl: undefined, dev: true }).eventsUrl).toBe('ws://127.0.0.1:8090/ws/events');
  });

  it('takes ?world=, ?cameras=, ?agent= and ?mode=video', () => {
    const c = readConfig('?world=127.0.0.1:18190&cameras=127.0.0.1:18181&agent=http://127.0.0.1:8002/&mode=video', page, {
      agentUrl: 'http://10.0.0.9:1',
      dev: false,
    });
    expect(c.eventsUrl).toBe('ws://127.0.0.1:18190/ws/events');
    expect(c.camerasUrl).toBe('http://127.0.0.1:18181');
    expect(c.agentUrl).toBe('http://127.0.0.1:8002');
    expect(c.agentHost).toBe('127.0.0.1:8002');
    expect(c.video).toBe(true);
  });

  it('takes ?panels= and ?masks=off; without them, every panel and the masks', () => {
    const c = readConfig('?mode=video&panels=floorplan, energy,,agent&masks=off', page, prod);
    expect(c.panels).toEqual(['floorplan', 'energy', 'agent']);
    expect(c.masks).toBe(false);
    const all = readConfig('?mode=video', page, prod);
    expect([all.panels, all.masks]).toEqual([null, true]);
  });

  it('takes ?privacy=on with an agent; without one it says no feed is shown', () => {
    const on = readConfig('?privacy=on&agent=http://127.0.0.1:8002', page, prod);
    expect([on.privacy, on.agentUrl, on.problems]).toEqual([true, 'http://127.0.0.1:8002', []]);
    const alone = readConfig('?privacy=on', page, prod);
    expect(alone.privacy).toBe(true);
    expect(alone.problems).toEqual(['?privacy=on needs ?agent=; no camera feed is shown']);
    expect(readConfig('?privacy=ON&agent=http://127.0.0.1:8002', page, prod).privacy).toBe(true);
    expect(readConfig('?privacy=yes', page, prod).privacy).toBe(false);
  });

  it('falls back to VITE_AGENT_URL, and reports a bad agent URL instead of using it', () => {
    expect(readConfig('', page, { agentUrl: 'http://10.1.2.3:8002', dev: false }).agentUrl).toBe('http://10.1.2.3:8002');
    const bad = readConfig('?agent=javascript:alert(1)', page, prod);
    expect(bad.agentUrl).toBeNull();
    expect(bad.agentProblem).toContain('not an http');
    expect(readConfig('', page, prod).agentUrl).toBeNull();
  });

  it('refuses an agent, a world or a camera server off this network, and says so', () => {
    const agent = readConfig('?agent=https://evil.example/collect', page, prod);
    expect(agent.agentUrl).toBeNull();
    expect(agent.agentProblem).toContain('not on this machine, a private (RFC 1918) or link-local address');
    expect(readConfig('', page, { agentUrl: 'http://8.8.8.8:8002', dev: false }).agentProblem).toContain('8.8.8.8');
    const world = readConfig('?world=evil.example:8090&cameras=203.0.113.5:8081', page, prod);
    expect(world.world).toBe('192.168.1.20:8090');
    expect(world.camerasUrl).toBe('http://192.168.1.20:8081');
    expect(world.problems).toHaveLength(2);
    expect(world.problems[0]).toContain('?world="evil.example:8090" is not');
    expect(readConfig('?world=192.168.1.20:8090/x?y', page, prod).problems[0]).toContain('is not host:port');
  });
});

describe('isLocalHost', () => {
  it.each([
    ['127.0.0.1', true],
    ['127.255.0.9', true],
    ['localhost', true],
    ['[::1]', true],
    ['10.0.0.1', true],
    ['172.16.0.1', true],
    ['172.31.255.255', true],
    ['192.168.43.1', true],
    ['169.254.1.1', true],
    ['[fe80::1]', true],
    ['[febf::1]', true],
    ['spotcool-pc', true], // the page's own host
    ['172.15.0.1', false],
    ['172.32.0.1', false],
    ['192.169.0.1', false],
    ['8.8.8.8', false],
    ['0.0.0.0', false],
    ['300.1.1.1', false],
    ['[fec0::1]', false],
    ['[2001:db8::1]', false],
    ['evil.example', false],
    ['localhost.evil.example', false],
  ])('%s: %s', (host, allowed) => {
    expect(isLocalHost(host, 'spotcool-pc')).toBe(allowed);
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
    expect(dayWindow(null, initialState.power)).toBeNull();
    expect(dayWindow(DAY + 9 * 3600, initialState.power)).toEqual([DAY + 7 * 3600, DAY + 19 * 3600]);
    expect(dayWindow(DAY + 20 * 3600, initialState.power)).toEqual([DAY + 7 * 3600, DAY + 20 * 3600]);
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
    expect(privacyUrl('http://h:8002', 'cam-meeting-live', 3)).toBe('http://h:8002/privacy/cam-meeting-live.jpg?n=3');
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
