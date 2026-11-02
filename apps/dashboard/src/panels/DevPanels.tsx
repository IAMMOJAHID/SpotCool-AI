import { memo } from 'react';
import { clockText } from '../format';
import type { DashboardState } from '../state/reducer';
import { Empty, Panel, Tag } from '../ui';

/** Operator view only: the world's errors and metrics, and what the feed dropped. */
export const DevPanels = memo(function DevPanels(props: {
  errors: DashboardState['errors'];
  metrics: DashboardState['metrics'];
  dropped: number;
}) {
  const errors = props.errors.slice(-50).reverse();
  const metrics = Object.entries(props.metrics).sort(([a], [b]) => a.localeCompare(b));
  return (
    <>
      <Panel
        id="errors"
        title="World errors"
        populated
        tags={props.dropped > 0 ? <Tag kind="warn">{props.dropped} invalid messages dropped</Tag> : undefined}
      >
        {errors.length === 0 ? (
          <Empty>No errors.</Empty>
        ) : (
          <ul className="errors">
            {errors.map(({ id, t, data }) => (
              <li key={id} className={data.fatal ? 'fatal' : ''}>
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
});
