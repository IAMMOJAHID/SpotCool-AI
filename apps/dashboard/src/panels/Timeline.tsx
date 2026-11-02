import { memo } from 'react';
import { clockText } from '../format';
import type { DashboardState } from '../state/reducer';
import { doorIntervals, meetings } from '../state/selectors';
import type { Axis, Interval } from '../state/selectors';
import { Empty, Panel } from '../ui';
import { zoneName, ZONES } from '../zones';

export const Timeline = memo(function Timeline(props: {
  simNow: number | null;
  axis: Axis | null;
  doors: DashboardState['doors'];
  calendar: DashboardState['calendar'];
  complaints: DashboardState['complaints'];
  video: boolean;
}) {
  const { axis, simNow: now, video } = props;
  if (!axis || now === null) {
    return (
      <Panel id="timeline" title="Timeline" populated={false}>
        <Empty>Waiting for the first step.</Empty>
      </Panel>
    );
  }
  const [x0, x1] = axis;
  const frac = (t: number) => (Math.min(Math.max(t, x0), x1) - x0) / (x1 - x0);
  const pct = (t: number) => `${(frac(t) * 100).toFixed(3)}%`;
  const span = (i: Interval) => ({ left: pct(i.from), width: `${((frac(i.to ?? now) - frac(i.from)) * 100).toFixed(3)}%` });
  // A label sits after its span, or before it near the right end.
  const side = (t: number) => (frac(t) > 0.62 ? 'before' : 'after');
  const doors = ZONES.flatMap((zone) => doorIntervals(props.doors[zone] ?? []).map((i) => ({ ...i, zone })));
  const booked = meetings(props.calendar);
  const latest = props.complaints.findLast((c) => c.t !== null);
  const hours: number[] = [];
  const stepS = video ? 10_800 : 3600;
  for (let t = Math.ceil(x0 / stepS) * stepS; t <= x1; t += stepS) hours.push(t);
  return (
    <Panel id="timeline" title="Timeline" populated>
      <div className="timeline" role="group" aria-label={`Timeline, now ${clockText(now)}`}>
        <div className="lane-label">Door</div>
        <div className="lane">
          {doors.map((d) => (
            <div key={`${d.zone}-${d.from}`} className="span span-door" style={span(d)}>
              <span className={`span-label label-${side(d.to ?? now)}`}>
                {zoneName(d.zone)} {clockText(d.from)}
                {'–'}
                {d.to === null ? 'now' : clockText(d.to)}
              </span>
            </div>
          ))}
        </div>
        <div className="lane-label">Meeting</div>
        <div className="lane">
          {booked.map((m) => (
            <div key={`${m.room}-${m.from}`} className="span span-meeting" style={span(m)} data-testid="meeting">
              <span className={`span-label label-${side(m.to ?? now)}`}>{m.subject ?? m.room}</span>
            </div>
          ))}
        </div>
        <div className="lane-label">Complaint</div>
        <div className="lane">
          {props.complaints.map((c) =>
            c.t === null ? null : (
              <div
                key={`${c.t}-${c.zone}-${c.text}`}
                className={`marker marker-${c.status}`}
                style={{ left: pct(c.t) }}
                data-testid="complaint-marker"
                data-status={c.status}
                title={`${clockText(c.t)} ${zoneName(c.zone)} (${c.channel}): "${c.text}" ${c.status}${c.detail ? `: ${c.detail}` : ''}`}
              >
                <span className="marker-dot" aria-hidden="true" />
              </div>
            ),
          )}
        </div>
        <div className="lane-label" aria-hidden="true" />
        <div className="axis" aria-hidden="true">
          {hours.map((t) => (
            <span key={t} style={{ left: pct(t) }}>
              {clockText(t)}
            </span>
          ))}
        </div>
        <div className="now-layer" aria-hidden="true">
          <div className="playhead" style={{ left: pct(now) }} data-testid="playhead">
            <span>{clockText(now)}</span>
          </div>
        </div>
      </div>
      {latest?.t != null && (
        <p className="complaint-caption" data-testid="complaint-caption">
          <span className={`marker-${latest.status}`}>
            <span className="marker-dot" aria-hidden="true" />
          </span>{' '}
          {clockText(latest.t)} {zoneName(latest.zone)}: {'“'}
          {latest.text}
          {'”'} <strong>{latest.status}</strong>
          {!video && latest.detail ? ` (${latest.detail})` : ''}
        </p>
      )}
    </Panel>
  );
});
