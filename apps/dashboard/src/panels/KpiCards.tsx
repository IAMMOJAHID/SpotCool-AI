import { memo } from 'react';
import { fixed } from '../format';
import type { DashboardState } from '../state/reducer';
import { Empty, Panel, Tag } from '../ui';
import type { KpiData, Run } from '../wire';

/** BOPTEST's KPI units (BOPTEST's KPI documentation); values are shown as BOPTEST reports them. */
const BOPTEST_UNITS: Record<string, string> = {
  ener_tot: 'kWh/m²',
  tdis_tot: 'K·h/zone',
  idis_tot: 'ppm·h/zone',
  cost_tot: 'cost/m²',
  emis_tot: 'kgCO₂/m²',
  pele_tot: 'kW/m²',
  pgas_tot: 'kW/m²',
  pdih_tot: 'kW/m²',
  time_rat: 's/s',
};
/** Why BOPTEST's discomfort KPIs are never shown bare (the lead's amendment to WP7). */
export const BOPTEST_CAPTION =
  "BOPTEST's schedule counts every zone as occupied all day, so its discomfort includes zones the scenario leaves empty.";
/** What a stub run's KPIs are worth, as the world's quote verdict says. */
export const STUB_KPI_NOTE = 'Stub agent: upper bound with perfect perception';
/** Shown instead of an RC run's figures: RC figures are on numbers.md's never-quote list. */
export const RC_NOTE = 'RC test plant: a model for testing, no saving is quoted';

/** The RC note, in place of a saving and of kWh. */
export function RcNote({ testId }: { testId: string }) {
  return (
    <p className="rc-note" data-testid={testId}>
      {RC_NOTE}
    </p>
  );
}

function Provisional({ kpi }: { kpi: KpiData | undefined }) {
  if (!kpi) return null;
  return kpi.provisional === false ? <Tag kind="final">final</Tag> : <Tag kind="provisional">provisional</Tag>;
}

export const KpiCards = memo(function KpiCards(props: {
  kpi: DashboardState['kpi'];
  stub: boolean;
  video: boolean;
  /** An RC run: no figure at all, the RC note instead. */
  rc: boolean;
}) {
  const { kpi, video } = props;
  const spotcool = kpi.spotcool?.data;
  const baseline = kpi.baseline?.data;
  const saved = spotcool?.energy_saved_pct;
  return (
    <Panel id="kpis" title="Results" populated={spotcool !== undefined}>
      {props.rc ? (
        <RcNote testId="kpi-rc" />
      ) : !spotcool && !baseline ? (
        <Empty>The first KPIs arrive after four steps.</Empty>
      ) : (
        <div className="cards">
          {props.stub && (
            <p className="kpi-stub" data-testid="kpi-stub">
              {STUB_KPI_NOTE}
            </p>
          )}
          <article className="card card-saved" aria-label="Energy saved">
            <h3>
              Energy saved <Provisional kpi={spotcool} />
            </h3>
            <p className="kpi-big" data-testid="energy-saved">
              {saved == null ? '—' : `${fixed(saved, 1)} %`}
            </p>
            <p className="kpi-note">SpotCool run against the baseline run</p>
          </article>
          <article className="card card-comfort" aria-label="Comfort where people were">
            <h3>
              Comfort where people were <Provisional kpi={spotcool ?? baseline} />
            </h3>
            <dl className="pair">
              {(['spotcool', 'baseline'] as Run[]).map((run) => (
                <div key={run} className={`pair-${run}`}>
                  <dt>{run === 'spotcool' ? 'SpotCool' : 'Baseline'}</dt>
                  <dd>
                    <span className="kpi-mid" data-testid={`comfort-${run}`}>
                      {fixed(kpi[run]?.data.comfort_where_people_kh, 2)}
                    </span>{' '}
                    K{'·'}h
                  </dd>
                </div>
              ))}
            </dl>
            {!video && (
              <p className="kpi-note">Kelvin-hours outside the comfort band where the scenario had people; lower is better</p>
            )}
          </article>
          {!video && <BoptestKpis spotcool={spotcool} baseline={baseline} />}
        </div>
      )}
    </Panel>
  );
});

/** Operator view only: BOPTEST's own KPIs once final, with the caption that qualifies them. */
function BoptestKpis({ spotcool, baseline }: { spotcool: KpiData | undefined; baseline: KpiData | undefined }) {
  const final = [spotcool, baseline].filter((k): k is KpiData => k?.provisional === false);
  const names = [...new Set(final.flatMap((k) => Object.keys(k.boptest)))];
  return (
    <article className="card card-boptest" aria-labelledby="boptest-title" data-testid="boptest-kpis">
      <h3 id="boptest-title">BOPTEST's own KPIs</h3>
      <p className="kpi-caption" data-testid="boptest-caption">
        {BOPTEST_CAPTION}
      </p>
      {names.length === 0 ? (
        <p className="kpi-note">Available when the run ends.</p>
      ) : (
        <table className="kpi-table">
          <thead>
            <tr>
              <th scope="col">KPI</th>
              <th scope="col">SpotCool</th>
              <th scope="col">Baseline</th>
            </tr>
          </thead>
          <tbody>
            {names.map((name) => (
              <tr key={name}>
                <th scope="row">
                  {name} <span className="sub">{BOPTEST_UNITS[name] ?? ''}</span>
                </th>
                <td>{fixed(spotcool?.boptest[name], 4)}</td>
                <td>{fixed(baseline?.boptest[name], 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </article>
  );
}
