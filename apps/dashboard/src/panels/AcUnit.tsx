import { capitalize, fixed } from '../format';
import type { DashboardState } from '../state/reducer';
import { useTween } from '../tween';
import { Empty, Panel } from '../ui';

/** `vrf_state` arrives every 200 ms while the unit moves: each new angle is eased in over that. */
export const VRF_PERIOD_MS = 200;

export function AcUnit({ state }: { state: DashboardState }) {
  const vrf = state.vrf?.data;
  const vertical = useTween(vrf?.vane_v_angle_deg ?? null, VRF_PERIOD_MS);
  // Daikin and LG have no horizontal louver: their state carries null, and none is drawn.
  const horizontal = useTween(vrf?.vane_h_angle_deg ?? null, VRF_PERIOD_MS);
  return (
    <Panel id="acunit" title="Meeting-room AC unit" populated={vrf !== undefined}>
      {!vrf ? (
        <Empty>Waiting for the VRF unit's state.</Empty>
      ) : (
        <div className="acunit">
          <svg viewBox="0 0 520 210" className="unit-svg" role="img" aria-label={`Vertical louver ${fixed(vrf.vane_v_angle_deg, 0)} degrees${vrf.vane_h_angle_deg === null ? '' : `, horizontal louver ${fixed(vrf.vane_h_angle_deg, 0)} degrees`}`}>
            <g className="side">
              <text x={20} y={202} className="view-label">side view</text>
              <line x1={18} y1={8} x2={18} y2={178} className="wall" />
              <rect x={18} y={28} width={150} height={60} rx={14} className="body" />
              <g transform={`translate(146 92) rotate(${-(vertical ?? 0)})`}>
                <line x1={0} y1={0} x2={120} y2={0} className="flow" />
                <polygon points="120,-9 138,0 120,9" className="flow-head" />
                <line x1={-6} y1={0} x2={44} y2={0} className="vane" />
              </g>
              <text x={186} y={46} className="angle">{`${fixed(vertical, 0)}°`}</text>
            </g>
            <g className="top">
              <text x={290} y={202} className="view-label">from above</text>
              {horizontal === null ? (
                <text x={400} y={110} textAnchor="middle" className="no-axis">
                  no horizontal louver
                </text>
              ) : (
                <>
                  <rect x={310} y={150} width={180} height={34} rx={10} className="body" />
                  {[-2, -1, 0, 1, 2].map((k) => (
                    <line key={k} x1={400 + k * 32} y1={146} x2={400 + k * 32} y2={122} className="vane" transform={`rotate(${horizontal} ${400 + k * 32} 146)`} />
                  ))}
                  <g transform={`translate(400 118) rotate(${horizontal})`}>
                    <line x1={0} y1={0} x2={0} y2={-80} className="flow" />
                    <polygon points="-9,-80 0,-98 9,-80" className="flow-head" />
                  </g>
                  <text x={418} y={52} className="angle">{`${fixed(horizontal, 0)}°`}</text>
                </>
              )}
            </g>
          </svg>
          <dl className="unit-facts">
            <div>
              <dt>Brand profile</dt>
              <dd data-testid="vrf-profile">{capitalize(vrf.profile)}</dd>
            </div>
            <div className="fact-extra">
              <dt>Unit</dt>
              <dd>
                {vrf.power_on ? 'On' : 'Off'} {'·'} {vrf.mode} {'·'} fan {vrf.fan}
              </dd>
            </div>
            <div>
              <dt>Setpoint / room</dt>
              <dd>
                {fixed(vrf.setpoint_c, 1)} {'°'}C / {fixed(vrf.room_temp_c, 1)} {'°'}C
              </dd>
            </div>
            <div>
              <dt>Louvers</dt>
              <dd>
                V {vrf.vane_v_pos}
                {vrf.vane_h_pos !== null && ` · H ${vrf.vane_h_pos}`}
              </dd>
            </div>
            <div className="fact-extra">
              <dt>Power</dt>
              <dd>{fixed(vrf.power_kw, 2)} kW</dd>
            </div>
          </dl>
        </div>
      )}
    </Panel>
  );
}
