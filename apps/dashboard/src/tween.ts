import { useEffect, useRef, useState } from 'react';

/** The value `elapsedMs` into a linear move from `from` to `to` lasting `durationMs`. */
export function tween(from: number, to: number, elapsedMs: number, durationMs: number): number {
  if (durationMs <= 0 || elapsedMs >= durationMs) return to;
  return from + ((to - from) * Math.max(0, elapsedMs)) / durationMs;
}

/**
 * `target`, eased in at the display's frame rate: each new target is reached `durationMs` after
 * it arrives, starting from the value shown at that moment.
 */
export function useTween(target: number | null, durationMs: number): number | null {
  const [shown, setShown] = useState(target);
  const shownRef = useRef(target);
  useEffect(() => {
    const from = shownRef.current ?? target;
    shownRef.current = target;
    if (target === null || from === null) return;
    const start = performance.now();
    let frame = requestAnimationFrame(function step(now: number) {
      const value = tween(from, target, now - start, durationMs);
      shownRef.current = value;
      setShown(value);
      if (value !== target) frame = requestAnimationFrame(step);
    });
    return () => {
      cancelAnimationFrame(frame);
    };
  }, [target, durationMs]);
  return target === null ? null : (shown ?? target);
}
