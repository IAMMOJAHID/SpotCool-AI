import { useEffect, useState } from 'react';
import type { CameraCatalog, CameraInfo } from './wire';
import { isRecord } from './wire';

/** The live phone camera filming the VRF demo; its tile shows only when the catalog lists it. */
export const LIVE_CAMERA_ID = 'cam-meeting-live';
const RETRY_MS = 5000;

/** A `GET /cameras` body as a catalog, keeping only well-formed cameras with an http(s) snapshot URL. */
export function parseCatalog(body: unknown): CameraCatalog | null {
  if (!isRecord(body) || !Array.isArray(body.cameras)) return null;
  const cameras = body.cameras.filter(
    (c): c is CameraInfo =>
      isRecord(c) &&
      typeof c.id === 'string' &&
      /^[a-z0-9][a-z0-9-]{0,63}$/.test(c.id) &&
      typeof c.name === 'string' &&
      typeof c.snapshot_url === 'string' &&
      /^https?:\/\//.test(c.snapshot_url) &&
      typeof c.width === 'number' &&
      typeof c.height === 'number',
  );
  return {
    cameras,
    ...(typeof body.privacy_notice === 'string' ? { privacy_notice: body.privacy_notice } : {}),
  };
}

/** A camera's MJPEG stream on the camera server (WP4's `/cameras/{id}/mjpeg`). */
export function mjpegUrl(camerasUrl: string, id: string): string {
  return `${camerasUrl}/cameras/${encodeURIComponent(id)}/mjpeg`;
}

/** A camera's latest Muse Image render on the agent's server; `n` makes each refresh a new URL. */
export function privacyUrl(agentUrl: string, id: string, n: number): string {
  return `${agentUrl}/privacy/${encodeURIComponent(id)}.jpg?n=${n}`;
}

/** The camera catalog, fetched again for each run and retried every 5 s until it answers. */
export function useCameraCatalog(
  camerasUrl: string,
  runId: string | null,
): { catalog: CameraCatalog | null; error: string | null } {
  const [result, setResult] = useState<{ catalog: CameraCatalog | null; error: string | null }>({
    catalog: null,
    error: null,
  });
  useEffect(() => {
    let timer: number | undefined;
    const controller = new AbortController();
    const load = async () => {
      try {
        const response = await fetch(`${camerasUrl}/cameras`, { signal: controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const catalog = parseCatalog(await response.json());
        if (!catalog) throw new Error('not a camera catalog');
        setResult({ catalog, error: null });
      } catch (error) {
        if (controller.signal.aborted) return;
        const reason = error instanceof Error ? error.message : String(error);
        setResult((held) => ({ catalog: held.catalog, error: `camera catalog at ${camerasUrl}: ${reason}` }));
        timer = window.setTimeout(() => void load(), RETRY_MS);
      }
    };
    void load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [camerasUrl, runId]);
  return result;
}
