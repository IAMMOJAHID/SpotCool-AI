import type { Config } from '../config';
import { backoffMs } from '../feed';
import { calendarDate, capitalize, clockText, dayOfYear, plantLabel, scenarioLabel } from '../format';
import type { DashboardState } from '../state/reducer';
import { Tag } from '../ui';

export function Header({ state, config }: { state: DashboardState; config: Config }) {
  const { run, simNow, connection, attempt } = state;
  // A dashboard that joins mid-run has missed the run's `running` event, but ticks show it runs.
  const status = run?.status ?? (simNow !== null ? 'running' : 'waiting');
  const link =
    connection === 'live'
      ? 'live'
      : connection === 'connecting'
        ? 'connecting'
        : `reconnecting (attempt ${attempt}, every ${backoffMs(attempt - 1) / 1000} s)`;
  return (
    <header className="top" data-testid="panel-header" data-populated={simNow !== null}>
      <div className="brand">
        <span className="logo">SpotCool</span>
        <span className="scenario" data-testid="scenario">
          {run ? scenarioLabel(run.scenario) : 'Scenario —'}
        </span>
        <span className="plant">{run ? plantLabel(run.plant) : 'Plant —'}</span>
      </div>
      <div className="clock" aria-label="Simulated time">
        <span className="time" data-testid="clock">
          {simNow === null ? '--:--' : clockText(simNow)}
        </span>
        <span className="day">
          {simNow === null ? 'waiting for the world' : `Day ${dayOfYear(simNow)} · ${calendarDate(simNow)}`}
        </span>
      </div>
      <div className="status">
        {state.stubAgent && (
          <Tag kind="stub">
            <span title="Perfect perception from the scenario script, not Person A's agent">Stub agent</span>
          </Tag>
        )}
        <span className={`run run-${status}`} data-testid="run-status" title={run?.message ?? undefined}>
          {capitalize(status)}
        </span>
        <span className={`link link-${connection}`} role="status" title={`World ${config.world}`}>
          <span className="dot" aria-hidden="true" />
          {link}
        </span>
        {!config.video && (
          <span className="meta">
            {run ? `run ${run.id}` : 'run id unknown'} {'·'} world {config.world}
            {run?.message ? ` · ${run.message}` : ''}
          </span>
        )}
      </div>
    </header>
  );
}
