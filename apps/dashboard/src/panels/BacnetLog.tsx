import { memo } from 'react';
import { bmsValue, clockText } from '../format';
import type { DashboardState } from '../state/reducer';
import { Empty, Panel } from '../ui';

/** Rows drawn: the newest; the state keeps more. */
export const SHOWN_ROWS = 50;
const VIDEO_ROWS = 3;

export const BacnetLog = memo(function BacnetLog(props: { rows: DashboardState['bacnet']; video: boolean }) {
  const { rows, video } = props;
  const shown = rows.slice(-(video ? VIDEO_ROWS : SHOWN_ROWS)).reverse();
  return (
    <Panel id="bacnet" title="BACnet writes" populated={rows.length > 0}>
      {rows.length === 0 ? (
        <Empty>No BACnet writes yet.</Empty>
      ) : (
        <div className="scroll">
          <table className="log">
            {video && (
              <colgroup>
                <col className="col-time" />
                <col className="col-object" />
                <col className="col-pri" />
                <col className="col-value" />
                <col className="col-result" />
              </colgroup>
            )}
            <thead>
              <tr>
                <th scope="col">Time</th>
                {!video && <th scope="col">Device</th>}
                <th scope="col">Object</th>
                <th scope="col">Pri</th>
                <th scope="col">Value</th>
                {!video && <th scope="col">Client</th>}
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {shown.map(({ id, t, data }) => {
                const value = bmsValue(data.value);
                const result = `${data.accepted ? 'accepted' : 'rejected'}${data.reason ? `: ${data.reason}` : ''}`;
                return (
                  <tr key={id} className={data.accepted ? '' : 'rejected'}>
                    <td>{t === null ? '—' : clockText(t)}</td>
                    {!video && <td>{data.device}</td>}
                    <td title={data.object_name}>
                      {data.object_name}
                      {!video && <span className="sub"> {data.object_id}</span>}
                    </td>
                    <td>{data.priority}</td>
                    <td className={data.value === null ? 'release' : ''} title={value}>
                      {value}
                    </td>
                    {!video && <td>{data.client}</td>}
                    <td title={result}>{result}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
});
