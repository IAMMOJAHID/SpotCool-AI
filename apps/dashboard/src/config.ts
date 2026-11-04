// Where the dashboard connects, from the page's URL (`?world=`, `?cameras=`, `?agent=`, `?mode=`).
// Only addresses on this machine or network are used: a crafted link cannot send the
// complaints, or the hold-to-talk audio, anywhere else.
//
// What it shows: `?panels=a,b,c` only those panels, filling the window, in the order the grid
// keeps (the ids: header, floorplan, cameras, kpis, energy, timeline, bacnet, acunit, agent,
// errors); `?masks=off` the camera tiles without SAM's boxes and counts, the raw feeds;
// `?privacy=on` (with `?agent=`) the camera tiles as the agent's Muse Image renders, never the
// raw feeds.

export interface Config {
  /** `host:port` of the world's dashboard server. */
  world: string;
  eventsUrl: string;
  /** Base URL of the world's camera server. */
  camerasUrl: string;
  /** Base URL of Person A's agent, or null when none is configured. */
  agentUrl: string | null;
  /** The agent's `host:port`, shown next to hold-to-talk. */
  agentHost: string | null;
  /** Why a configured agent URL was not used. */
  agentProblem: string | null;
  /** Why a `?world=` or `?cameras=` was not used. */
  problems: string[];
  video: boolean;
  /** The panels to show, filling the window; null for the whole layout. */
  panels: string[] | null;
  /** Whether the camera tiles draw SAM's boxes and counts. */
  masks: boolean;
  /** Whether the camera tiles show the agent's privacy renders in place of the raw feeds. */
  privacy: boolean;
}

/** The world's dashboard server in development, when `?world=` is not given. */
export const DEV_WORLD = '127.0.0.1:8090';
/** The world's HTTP port: camera catalog, snapshots and MJPEG (`WorldSettings.http_port`). */
export const CAMERA_PORT = 8081;
const ALLOWED = 'this machine, a private (RFC 1918) or link-local address, or the page’s own host';
const IPV4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;

const bare = (host: string) => host.toLowerCase().replace(/^\[|\]$/g, '');

/**
 * Whether the dashboard may connect to `hostname`: loopback, RFC 1918, link-local (IPv4 and
 * IPv6), or the host the page itself came from.
 */
export function isLocalHost(hostname: string, pageHostname: string): boolean {
  const host = bare(hostname);
  if (host === 'localhost' || (host !== '' && host === bare(pageHostname))) return true;
  const v4 = host.match(IPV4);
  if (v4) {
    const [a = 256, b = 256, c = 256, d = 256] = v4.slice(1).map(Number);
    if ([a, b, c, d].some((octet) => octet > 255)) return false;
    return a === 127 || a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254);
  }
  return host === '::1' || /^fe[89ab][0-9a-f]:/.test(host); // fe80::/10
}

/** A `host:port` parameter, normalized, or why it is refused. */
function hostParam(value: string, pageHostname: string): { host: string } | { problem: string } {
  let url: URL;
  try {
    url = new URL(`http://${value}`);
  } catch {
    return { problem: `"${value}" is not host:port` };
  }
  if (url.pathname !== '/' || url.search || url.hash || url.username || url.password) {
    return { problem: `"${value}" is not host:port` };
  }
  if (!isLocalHost(url.hostname, pageHostname)) return { problem: `"${value}" is not ${ALLOWED}` };
  return { host: url.host };
}

/** An agent base URL, without its trailing slash, or why it is refused. */
function agentParam(value: string, pageHostname: string): { url: string; host: string } | { problem: string } {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return { problem: `"${value}" is not an http or https URL` };
  }
  if ((url.protocol !== 'http:' && url.protocol !== 'https:') || url.username || url.password) {
    return { problem: `"${value}" is not an http or https URL` };
  }
  if (!isLocalHost(url.hostname, pageHostname)) return { problem: `"${value}" is not on ${ALLOWED}` };
  return { url: url.href.replace(/\/+$/, ''), host: url.host };
}

/**
 * The dashboard's connections.
 *
 * @param search The page's query string.
 * @param page The page's own host, host name and protocol: WP6's dashboard server serves the
 *   built app, so in production the world is the page's host.
 * @param env `VITE_AGENT_URL`, and whether this is Vite's development server.
 */
export function readConfig(
  search: string,
  page: { host: string; hostname: string; protocol: string },
  env: { agentUrl: string | undefined; dev: boolean },
): Config {
  const query = new URLSearchParams(search);
  const problems: string[] = [];
  const pick = (name: string, fallback: string): string => {
    const value = query.get(name);
    if (value === null) return fallback;
    const checked = hostParam(value, page.hostname);
    if ('host' in checked) return checked.host;
    problems.push(`?${name}=${checked.problem}; using ${fallback}`);
    return fallback;
  };
  const world = pick('world', env.dev ? DEV_WORLD : page.host);
  const cameras = pick('cameras', `${world.replace(/:\d+$/, '')}:${CAMERA_PORT}`);
  const secure = page.protocol === 'https:';
  const agentValue = query.get('agent') ?? env.agentUrl;
  const agent = agentValue ? agentParam(agentValue, page.hostname) : null;
  const privacy = query.get('privacy') === 'on';
  if (privacy && !(agent && 'url' in agent)) problems.push('?privacy=on needs ?agent=; no camera feed is shown');
  return {
    world,
    eventsUrl: `${secure ? 'wss' : 'ws'}://${world}/ws/events`,
    camerasUrl: `${secure ? 'https' : 'http'}://${cameras}`,
    agentUrl: agent && 'url' in agent ? agent.url : null,
    agentHost: agent && 'url' in agent ? agent.host : null,
    agentProblem: agent && 'problem' in agent ? agent.problem : null,
    problems,
    video: query.get('mode') === 'video',
    panels: query.get('panels')?.split(',').map((id) => id.trim()).filter(Boolean) ?? null,
    masks: query.get('masks') !== 'off',
    privacy,
  };
}
