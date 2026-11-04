import { memo, useEffect, useState } from 'react';
import { LIVE_CAMERA_ID, mjpegUrl, privacyUrl } from '../cameras';
import type { DashboardState, Masks } from '../state/reducer';
import { Empty, Panel, StubTag, Tag } from '../ui';
import type { CameraCatalog, CameraInfo } from '../wire';
import { zoneName } from '../zones';

const SNAPSHOT_POLL_MS = 1000;
/** A Muse Image render takes about 24 s, and the agent renders each camera every 3 minutes. */
const RENDER_POLL_MS = 10_000;

export const CameraWall = memo(function CameraWall(props: {
  catalog: CameraCatalog | null;
  error: string | null;
  camerasUrl: string;
  runId: string | null;
  people: DashboardState['people'];
  masks: DashboardState['masks'];
  scenes: DashboardState['scenes'];
  script: DashboardState['script'];
  video: boolean;
  /** Draw SAM's boxes and the counts; off for the raw feeds (`?masks=off`). */
  showMasks: boolean;
  /** Show the agent's privacy renders, never the raw feeds (`?privacy=on`). */
  privacy: boolean;
  /** The agent's base URL, where the renders are; null when none is configured. */
  agentUrl: string | null;
}) {
  const { catalog, video, showMasks } = props;
  // Zone cameras always; the live phone only when the catalog lists it.
  const cameras = catalog?.cameras.filter((c) => c.zone != null || c.id === LIVE_CAMERA_ID) ?? [];
  return (
    <Panel id="cameras" title="Cameras" populated={cameras.length > 0}>
      {cameras.length === 0 ? (
        <Empty>{props.error ?? 'Loading the camera catalog…'}</Empty>
      ) : (
        <div className="tiles">
          {cameras.map((camera) => (
            <CameraTile
              key={`${props.runId ?? ''}-${camera.id}`}
              camera={camera}
              // A video tile is too narrow for the catalog's name beside the count: its zone's name.
              label={video && camera.zone ? zoneName(camera.zone) : camera.name}
              camerasUrl={props.camerasUrl}
              privacy={props.privacy}
              agentUrl={props.agentUrl}
              people={camera.zone && showMasks ? props.people[camera.zone] : undefined}
              masks={showMasks ? props.masks[camera.id] : undefined}
              script={
                video
                  ? null
                  : {
                      scene: props.scenes[camera.id]?.scene ?? null,
                      people: props.script?.zones.find((z) => z.zone === camera.zone)?.people ?? null,
                    }
              }
            />
          ))}
        </div>
      )}
      {/* The catalog's notice is for operators (contracts' cameras.py). */}
      {!video && catalog?.privacy_notice && <p className="privacy">{catalog.privacy_notice}</p>}
    </Panel>
  );
});

function CameraTile(props: {
  camera: CameraInfo;
  label: string;
  camerasUrl: string;
  privacy: boolean;
  agentUrl: string | null;
  people: { people: number; stub: boolean } | undefined;
  masks: Masks | undefined;
  script: { scene: string | null; people: number | null } | null;
}) {
  const { camera, people, masks, script, privacy, agentUrl } = props;
  // MJPEG first (an <img> needs no CORS); if the stream fails, a snapshot every second. A frame
  // that failed is hidden behind "no signal" (a broken image would show its alt text) until the
  // next one loads.
  const [polling, setPolling] = useState(false);
  const [frame, setFrame] = useState(0);
  const [lost, setLost] = useState(false);
  useEffect(() => {
    if (!polling) return;
    const timer = window.setInterval(() => {
      setFrame((n) => n + 1);
    }, SNAPSHOT_POLL_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [polling]);
  // With `?privacy=on`, only the agent's render, every 10 s; "rendering…" until one loads.
  const [rendered, setRendered] = useState(false);
  useEffect(() => {
    if (!privacy) return;
    const timer = window.setInterval(() => {
      setFrame((n) => n + 1);
    }, RENDER_POLL_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [privacy]);
  if (privacy) {
    return (
      <figure className="tile" data-camera={camera.id}>
        <div className="frame">
          {agentUrl && (
            <img
              src={privacyUrl(agentUrl, camera.id, frame)}
              alt={`${camera.name} camera, Muse Image render`}
              decoding="async"
              className={rendered ? undefined : 'lost'}
              onLoad={() => {
                setRendered(true);
              }}
              onError={() => {
                setRendered(false);
              }}
            />
          )}
          {!rendered && <span className="cam-lost">{agentUrl ? 'rendering…' : 'no agent'}</span>}
          <Overlay camera={camera} masks={masks} people={people} />
          {/* Over the frame: a second caption line doesn't fit the video mode's tiles. The boxes and the count are live, the render minutes behind. */}
          {rendered && <span className="cam-age">up to ~180 s old</span>}
        </div>
        <figcaption>
          <span className="cam-name" title={camera.name}>
            {props.label}
          </span>
          <span className="cam-note">Muse Image render</span>
        </figcaption>
      </figure>
    );
  }
  const src = polling
    ? `${camera.snapshot_url}${camera.snapshot_url.includes('?') ? '&' : '?'}frame=${frame}`
    : mjpegUrl(props.camerasUrl, camera.id);
  const live = camera.id === LIVE_CAMERA_ID;
  return (
    <figure className={`tile${live ? ' tile-live' : ''}`} data-camera={camera.id}>
      <div className="frame">
        <img
          src={src}
          alt={`${camera.name} camera`}
          decoding="async"
          className={lost ? 'lost' : undefined}
          onLoad={() => {
            setLost(false);
          }}
          onError={() => {
            setPolling(true);
            setLost(true);
          }}
        />
        {lost && <span className="cam-lost">no signal</span>}
        <Overlay camera={camera} masks={masks} people={people} />
        {live && !camera.name.toLowerCase().includes('recorded') && <Tag kind="live">live</Tag>}
      </div>
      <figcaption>
        <span className="cam-name" title={camera.name}>
          {props.label}
        </span>
        {script && (script.scene !== null || script.people !== null) && (
          <span className="cam-script">
            <Tag kind="script">script</Tag> {script.scene ?? '?'} {'·'} {script.people ?? '?'}
          </span>
        )}
        {polling && <span className="cam-note">snapshots</span>}
      </figcaption>
    </figure>
  );
}

/** SAM's boxes and the count, drawn over a tile's image. */
function Overlay(props: {
  camera: CameraInfo;
  masks: Masks | undefined;
  people: { people: number; stub: boolean } | undefined;
}) {
  const { camera, masks, people } = props;
  return (
    <>
      {masks && masks.polygons.length > 0 && (
        <svg
          className="masks"
          viewBox={`0 0 ${camera.width} ${camera.height}`}
          preserveAspectRatio="xMidYMid slice"
          aria-hidden="true"
        >
          {masks.polygons.map((polygon, i) => (
            <polygon key={i} points={polygon.map(([x, y]) => `${x},${y}`).join(' ')} />
          ))}
        </svg>
      )}
      {people && (
        <span className="cam-count" data-testid="camera-count">
          {people.people} {people.people === 1 ? 'person' : 'people'}
          {people.stub && <StubTag />}
        </span>
      )}
    </>
  );
}
