import { memo, useEffect, useState } from 'react';
import { LIVE_CAMERA_ID, mjpegUrl } from '../cameras';
import type { DashboardState, Masks } from '../state/reducer';
import { Empty, Panel, StubTag, Tag } from '../ui';
import type { CameraCatalog, CameraInfo } from '../wire';
import { zoneName } from '../zones';

const SNAPSHOT_POLL_MS = 1000;

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
}) {
  const { catalog, video } = props;
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
              people={camera.zone ? props.people[camera.zone] : undefined}
              masks={props.masks[camera.id]}
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
  people: { people: number; stub: boolean } | undefined;
  masks: Masks | undefined;
  script: { scene: string | null; people: number | null } | null;
}) {
  const { camera, people, masks, script } = props;
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
        {live && <Tag kind="live">live</Tag>}
        {people && (
          <span className="cam-count" data-testid="camera-count">
            {people.people} {people.people === 1 ? 'person' : 'people'}
            {people.stub && <StubTag />}
          </span>
        )}
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
