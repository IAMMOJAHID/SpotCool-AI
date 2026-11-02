import { bmsValue, clockText } from '../format';
import type { DashboardState } from '../state/reducer';
import { Empty, Panel } from '../ui';

const VIDEO_ROWS = 3;

export function BacnetLog({ state, video }: { state: DashboardState; video: boolean }) {
  const rows = [...state.bacnet].reverse();
  const shown = video ? rows.slice(0, VIDEO_ROWS) : rows;
  return (
    <Panel id="bacnet" title="BACnet writes" populated={rows.length > 0}>
      {rows.length === 0 ? (
        <Empty>No BACnet writes yet.</Empty>
      ) : (
        <div className="scroll">
          <table className="log">
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
              {shown.map(({ tMs, t, data }, i) => (
                <tr key={`${tMs}-${i}`} className={data.accepted ? '' : 'rejected'}>
                  <td>{t === null ? '—' : clockText(t)}</td>
                  {!video && <td>{data.device}</td>}
                  <td>
                    {data.object_name}
                    {!video && <span className="sub"> {data.object_id}</span>}
                  </td>
                  <td>{data.priority}</td>
                  <td className={data.value === null ? 'release' : ''}>{bmsValue(data.value)}</td>
                  {!video && <td>{data.client}</td>}
                  <td>
                    {data.accepted ? 'accepted' : 'rejected'}
                    {data.reason && <span className="sub">: {data.reason}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
