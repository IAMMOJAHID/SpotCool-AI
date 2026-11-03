// A stand-in for the world's two servers, for development and the Playwright tests.
//
//   node scripts/mock-feed.mjs [recording.jsonl] [--port 8790] [--host 127.0.0.1] [--speed 300]
//
// - ws://<host>:<port>/ws/events replays a WP6 recording's event records ({"kind": "event",
//   "envelope": ...}; raw records are skipped) to each client from the start, waiting before each
//   event the sim time it moves the clock on, divided by --speed (sim seconds per wall second;
//   0 sends everything at once). Without a file it plays fixtures/sample-events.jsonl.
// - /cameras, /cameras/{id}/mjpeg and /cameras/{id}/snapshot.jpg stand in for WP4's camera server
//   with plain generated frames (PNG), so the camera wall is live too: open the dashboard with
//   ?world=<host>:<port>&cameras=<host>:<port>.
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { crc32, deflateSync } from 'node:zlib';
import { WebSocketServer } from 'ws';

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    port: { type: 'string', default: '8790' },
    host: { type: 'string', default: '127.0.0.1' },
    speed: { type: 'string', default: '300' },
  },
});
const port = Number(values.port);
const speed = Number(values.speed);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error(`--port ${values.port} is not a port`);
if (!(speed >= 0)) throw new Error(`--speed ${values.speed} must be 0 or more`);
const file = positionals[0] ?? fileURLToPath(new URL('../fixtures/sample-events.jsonl', import.meta.url));

/** The recording's envelopes, in order; torn or raw lines are skipped. */
const envelopes = readFileSync(file, 'utf8')
  .split('\n')
  .flatMap((line) => {
    try {
      const record = JSON.parse(line);
      return record.kind === 'event' && record.envelope ? [record.envelope] : [];
    } catch {
      return [];
    }
  });

const ZONE_CAMERAS = [
  ['cam-cor', 'cor', 'Core: Open-plan workspace', [52, 70, 92]],
  ['cam-nor', 'nor', 'North: Team area', [44, 82, 74]],
  ['cam-sou', 'sou', 'South: Meeting room', [86, 64, 96]],
  ['cam-eas', 'eas', 'East: Team area', [92, 78, 52]],
  ['cam-wes', 'wes', 'West: Reception and entrance', [96, 56, 60]],
];
const CAMERAS = [
  ...ZONE_CAMERAS.map(([id, zone, name, rgb]) => ({ id, zone, name, rgb, source: 'scenario' })),
  { id: 'cam-meeting-live', zone: null, name: 'Meeting room: live phone', rgb: [40, 40, 48], source: 'live' },
];

/** A plain 320x180 PNG: the camera's colour, a lighter window band and a darker floor. */
function frame([r, g, b]) {
  const w = 320;
  const h = 180;
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) {
    const shade = y < 40 ? 1.5 : y > 120 ? 0.6 : 1;
    for (let x = 0; x < w; x++) {
      const o = y * (w * 3 + 1) + 1 + x * 3;
      raw[o] = Math.min(255, r * shade);
      raw[o + 1] = Math.min(255, g * shade);
      raw[o + 2] = Math.min(255, b * shade);
    }
  }
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(body));
    return Buffer.concat([length, body, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(w, 0);
  header.writeUInt32BE(h, 4);
  header.set([8, 2, 0, 0, 0], 8); // 8-bit RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}
const FRAMES = new Map(CAMERAS.map((c) => [c.id, frame(c.rgb)]));
const CAMERA_PATH = /^\/cameras\/([a-z0-9-]+)\/(mjpeg|snapshot\.jpg)$/;

const server = createServer((request, response) => {
  const url = new URL(request.url ?? '/', `http://${request.headers.host ?? 'localhost'}`);
  const cors = { 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' };
  if (url.pathname === '/healthz') {
    response.writeHead(200, { ...cors, 'Content-Type': 'application/json' }).end('{"status":"ok"}');
    return;
  }
  if (url.pathname === '/cameras') {
    const cameras = CAMERAS.map(({ id, zone, name, source }) => ({
      id,
      name,
      zone,
      snapshot_url: `${url.origin}/cameras/${id}/snapshot.jpg`,
      stream_url: null,
      width: 1280,
      height: 720,
      fps_hint: 5,
      source,
    }));
    const notice = 'Frames are processed in memory and never stored; only counts leave.';
    response.writeHead(200, { ...cors, 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ cameras, privacy_notice: notice }));
    return;
  }
  const match = url.pathname.match(CAMERA_PATH);
  const image = match && FRAMES.get(match[1]);
  if (!image) {
    response.writeHead(404, cors).end('not found');
    return;
  }
  if (match[2] === 'snapshot.jpg') {
    response.writeHead(200, { ...cors, 'Content-Type': 'image/png' }).end(image);
    return;
  }
  response.writeHead(200, { ...cors, 'Content-Type': 'multipart/x-mixed-replace; boundary=frame' });
  const head = Buffer.from(`--frame\r\nContent-Type: image/png\r\nContent-Length: ${image.length}\r\n\r\n`);
  const part = () => response.write(Buffer.concat([head, image, Buffer.from('\r\n')]));
  part();
  const timer = setInterval(part, 1000);
  request.on('close', () => clearInterval(timer));
});

const sockets = new WebSocketServer({ server, path: '/ws/events' });
sockets.on('connection', (socket) => {
  let i = 0;
  let clock = null; // the latest sim time played
  let timer;
  const send = (envelope) => {
    if (socket.readyState === socket.OPEN) socket.send(JSON.stringify(envelope));
  };
  const next = () => {
    while (i < envelopes.length && socket.readyState === socket.OPEN) {
      const envelope = envelopes[i];
      i += 1;
      const t = envelope.sim_time_s;
      const gapMs = speed > 0 && t != null && clock != null && t > clock ? ((t - clock) / speed) * 1000 : 0;
      if (t != null) clock = clock == null ? t : Math.max(clock, t);
      if (gapMs > 0) {
        timer = setTimeout(() => {
          send(envelope);
          next();
        }, gapMs);
        return;
      }
      send(envelope);
    }
  };
  socket.on('close', () => clearTimeout(timer));
  next();
});

server.listen(port, values.host, () => {
  console.log(`mock feed: ${envelopes.length} events from ${file} at speed ${speed}`);
  console.log(`  ws://${values.host}:${port}/ws/events and http://${values.host}:${port}/cameras`);
});
