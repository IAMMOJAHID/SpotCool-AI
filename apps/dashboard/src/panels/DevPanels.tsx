import { clockText } from '../format';
import type { DashboardState } from '../state/reducer';
import { Empty, Panel, Tag } from '../ui';

/** Operator view only: the world's errors and metrics, and what the feed dropped. */
export function DevPanels({ state }: { state: DashboardState }) {
  const errors = [...state.errors].reverse();
  const metrics = Object.entries(state.metrics).sort(([a], [b]) => a.localeCompare(b));
  return (
    <>
      <Panel
        id="errors"
        title="World errors"
        populated
        tags={state.dropped > 0 ? <Tag kind="warn">{state.dropped} invalid messages dropped</Tag> : undefined}
      >
        {errors.length === 0 ? (
          <Empty>No errors.</Empty>
        ) : (
          <ul className="errors">
            {errors.map(({ tMs, t, data }, i) => (
              <li key={`${tMs}-${i}`} className={data.fatal ? 'fatal' : ''}>
                <span className="feed-time">{t === null ? '' : clockText(t)}</span>
                {data.fatal && <Tag kind="warn">fatal</Tag>} <strong>{data.where}</strong>: {data.message}
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Panel id="metrics" title="World metrics" populated>
        {metrics.length === 0 ? (
          <Empty>No metrics yet.</Empty>
        ) : (
          <table className="log">
            <tbody>
              {metrics.map(([key, { data }]) => (
                <tr key={key}>
                  <th scope="row">{key}</th>
                  <td>
                    {Number(data.value.toPrecision(4))} {data.unit}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}
