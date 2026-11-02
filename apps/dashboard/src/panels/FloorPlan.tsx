import { inkFor, tempColor, tempGradient } from '../colors';
import { fixed } from '../format';
import type { DashboardState } from '../state/reducer';
import { Panel, StubTag, Tag } from '../ui';
import type { Zone } from '../wire';
import { zoneName, zoneRole, ZONES } from '../zones';

// A schematic of BOPTEST's multizone office: the core in the centre, the four perimeter zones
// around it (north up). Perimeter depth is drawn wider than to scale, for the labels.
const W = 640;
const H = 400;
const C = { x0: 150, y0: 115, x1: 490, y1: 285 };
const SHAPES: Record<Zone, { points: string; x: number; y: number; narrow: boolean }> = {
  cor: { points: `${C.x0},${C.y0} ${C.x1},${C.y0} ${C.x1},${C.y1} ${C.x0},${C.y1}`, x: 320, y: C.y0 + 32, narrow: false },
  nor: { points: `0,0 ${W},0 ${C.x1},${C.y0} ${C.x0},${C.y0}`, x: 320, y: 26, narrow: false },
  sou: { points: `${C.x0},${C.y1} ${C.x1},${C.y1} ${W},${H} 0,${H}`, x: 320, y: C.y1 + 28, narrow: false },
  wes: { points: `0,0 ${C.x0},${C.y0} ${C.x0},${C.y1} 0,${H}`, x: 72, y: C.y0 + 20, narrow: true },
  eas: { points: `${W},0 ${W},${H} ${C.x1},${C.y1} ${C.x1},${C.y0}`, x: 568, y: C.y0 + 20, narrow: true },
};
const MAX_ICONS = 6;
const ICON_W = 22;

function People({ count, narrow }: { count: number; narrow: boolean }) {
  if (count === 0) return <text y={20} className="nobody">nobody</text>;
  const n = Math.min(count, MAX_ICONS);
  const perRow = narrow ? 3 : MAX_ICONS;
  return (
    <g aria-label={`${count} people seen by the agent`}>
      {Array.from({ length: n }, (_, i) => {
        const row = Math.floor(i / perRow);
        const inRow = Math.min(perRow, n - row * perRow);
        return <use key={i} href="#person" className="person" x={((i % perRow) - inRow / 2) * ICON_W} y={row * 30} width={20} height={28} />;
      })}
      {count > MAX_ICONS && <text x={(Math.min(perRow, n) / 2) * ICON_W + 18} y={20}>{`+${count - MAX_ICONS}`}</text>}
    </g>
  );
}

export function FloorPlan({ state, video }: { state: DashboardState; video: boolean }) {
  const measured = state.latest.spotcool;
  const zones = new Map(measured?.zones.map((z) => [z.zone, z]));
  const script = new Map(state.script?.zones.map((z) => [z.zone, z]));
  const stub = Object.values(state.people).some((s) => s.stub);
  return (
    <Panel id="floorplan" title={'Floor plan · SpotCool run'} populated={measured !== null} tags={stub ? <StubTag /> : undefined}>
      <svg className="floor" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Zone temperatures, setpoints and people">
        <defs>
          <symbol id="person" viewBox="0 0 20 32">
            <circle cx="10" cy="6" r="5.5" />
            <path d="M2 31 v-11 a8 8 0 0 1 16 0 v11 z" />
          </symbol>
        </defs>
        {ZONES.map((zone) => {
          const { points, x, y, narrow } = SHAPES[zone];
          const m = zones.get(zone);
          const fill = m ? tempColor(m.temp_c) : '#1b2430';
          const people = state.people[zone]?.people;
          const truth = script.get(zone);
          return (
            <g key={zone} data-zone={zone}>
              <polygon points={points} fill={fill} className="zone-shape">
                <title>{`${zoneName(zone)}: ${m ? `${fixed(m.temp_c, 1)} °C` : 'no data'}`}</title>
              </polygon>
              <g fill={m ? inkFor(fill) : '#c9d4e0'} textAnchor="middle" transform={`translate(${x} ${y})`}>
                <text className="zone-name">{narrow ? zoneName(zone) : `${zoneName(zone)} · ${zoneRole(zone)}`}</text>
                <text className="zone-temp" x={narrow ? 0 : -44} y={38}>
                  {m ? `${fixed(m.temp_c, 1)}°` : '—'}
                </text>
                {m && (
                  <g className="badge" transform={narrow ? 'translate(0 64)' : 'translate(62 30)'}>
                    <rect x={-44} y={-18} width={88} height={25} rx={6} />
                    <text y={1}>{`SP ${fixed(m.cooling_sp_c, 1)}`}</text>
                  </g>
                )}
                <g transform={`translate(0 ${narrow ? 78 : 50})`}>
                  {people !== undefined && <People count={people} narrow={narrow} />}
                  {!video && truth && (
                    <text className="script-count" x={narrow ? 0 : 118} y={narrow ? 78 : 20}>{`script: ${truth.people}`}</text>
                  )}
                </g>
              </g>
            </g>
          );
        })}
      </svg>
      <div className="legend">
        <span>22 {'°'}C</span>
        <span className="scale" style={{ background: tempGradient() }} aria-hidden="true" />
        <span>30 {'°'}C</span>
        <span className="legend-note">
          SP cooling setpoint {'·'} people as the agent counts them
          {!video && (
            <>
              {' · '}
              <Tag kind="script">script</Tag> the scenario's true count
            </>
          )}
        </span>
      </div>
    </Panel>
  );
}
