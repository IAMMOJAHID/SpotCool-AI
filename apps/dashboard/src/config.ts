// Where the dashboard connects, from the page's URL (`?world=`, `?cameras=`, `?agent=`, `?mode=`).

export interface Config {
  /** `host:port` of the world's dashboard server. */
  world: string;
  eventsUrl: string;
  /** Base URL of the world's camera server. */
  camerasUrl: string;
  /** Base URL of Person A's agent, or null when none is configured. */
  agentUrl: string | null;
  /** Why a configured agent URL was not used. */
  agentProblem: string | null;
  video: boolean;
}

/** The world's dashboard server in development, when `?world=` is not given. */
export const DEV_WORLD = '127.0.0.1:8090';
/** The world's HTTP port: camera catalog, snapshots and MJPEG (`WorldSettings.http_port`). */
export const CAMERA_PORT = 8081;

const HOST_PORT = /^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$/;

function hostParam(value: string | null): string | null {
  return value !== null && HOST_PORT.test(value) ? value : null;
}

/**
 * The dashboard's connections.
 *
 * @param search The page's query string.
 * @param page The page's own host and protocol: WP6's dashboard server serves the built app, so
 *   in production the world is the page's host.
 * @param env `VITE_AGENT_URL`, and whether this is Vite's development server.
 */
export function readConfig(
  search: string,
  page: { host: string; protocol: string },
  env: { agentUrl: string | undefined; dev: boolean },
): Config {
  const query = new URLSearchParams(search);
  const world = hostParam(query.get('world')) ?? (env.dev ? DEV_WORLD : page.host);
  const cameras = hostParam(query.get('cameras')) ?? `${world.replace(/:\d+$/, '')}:${CAMERA_PORT}`;
  const secure = page.protocol === 'https:';
  const agent = agentUrl(query.get('agent') ?? env.agentUrl);
  return {
    world,
    eventsUrl: `${secure ? 'wss' : 'ws'}://${world}/ws/events`,
    camerasUrl: `${secure ? 'https' : 'http'}://${cameras}`,
    agentUrl: agent.url,
    agentProblem: agent.problem,
    video: query.get('mode') === 'video',
  };
}

function agentUrl(value: string | undefined): { url: string | null; problem: string | null } {
  if (!value) return { url: null, problem: null };
  try {
    const url = new URL(value);
    if (url.protocol === 'http:' || url.protocol === 'https:') {
      return { url: url.href.replace(/\/+$/, ''), problem: null };
    }
  } catch {
    // reported below
  }
  return { url: null, problem: `"${value}" is not an http or https URL` };
}
