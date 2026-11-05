import { memo } from 'react';
import type { Config } from '../config';
import { backoffMs } from '../feed';
import { calendarDate, capitalize, clockText, dayOfYear, plantLabel, scenarioLabel } from '../format';
import type { Connection, RunInfo } from '../state/reducer';
import { Tag } from '../ui';

export const Header = memo(function Header(props: {
  run: RunInfo | null;
  simNow: number | null;
  connection: Connection;
  attempt: number;
  stub: boolean;
  config: Config;
}) {
  const { run, simNow, connection, attempt, config } = props;
  // Ticks without a run status (a world that does not send the run so far) still show it runs.
  const status = run?.status ?? (simNow !== null ? 'running' : 'waiting');
  const link =
    connection === 'live'
      ? 'live'
      : connection === 'connecting'
        ? 'connecting'
        : `reconnecting (attempt ${attempt}, every ${backoffMs(attempt - 1) / 1000} s)`;
  const scenario = run ? scenarioLabel(run.scenario) : 'Scenario —';
  return (
    <header className="top" data-testid="panel-header" data-populated={simNow !== null}>
      <div className="brand">
        <span className="logo">SpotCool</span>
        <span className="scenario" data-testid="scenario" title={scenario}>
          {scenario}
        </span>
        <span className="plant">{run ? plantLabel(run.plant) : 'Plant —'}</span>
      </div>
      <div className="clock" aria-label="Simulated time">
        <span className="time" data-testid="clock">
          {simNow === null ? '--:--' : clockText(simNow)}
        </span>
        <span className="day" data-testid="day">
          {simNow === null ? 'waiting for the world' : `Day ${dayOfYear(simNow)} · ${calendarDate(simNow)}`}
        </span>
      </div>
      <div className="status">
        {props.stub && (
          <Tag kind="stub">
            <span title="Perfect perception from the scenario script, not SpotCool's agent">Stub agent</span>
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
      {config.problems.length > 0 && (
        <p className="config-problems" role="alert" data-testid="config-problems">
          {config.problems.join(' · ')}
        </p>
      )}
    </header>
  );
});
