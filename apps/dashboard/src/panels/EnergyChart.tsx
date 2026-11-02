import { Chart, Legend, LinearScale, LineController, LineElement, PointElement, Tooltip } from 'chart.js';
import type { ChartData, ChartOptions } from 'chart.js';
import { Line } from 'react-chartjs-2';
import { RUN_COLOR } from '../colors';
import { clockText, fixed } from '../format';
import type { DashboardState } from '../state/reducer';
import { dayWindow } from '../state/selectors';
import { Empty, Panel } from '../ui';
import type { Run } from '../wire';

Chart.register(LineController, LineElement, PointElement, LinearScale, Legend, Tooltip);

const RUNS: ReadonlyArray<[Run, string]> = [
  ['spotcool', 'SpotCool'],
  ['baseline', 'Baseline'],
];

/** Two sim-time series per run: power (each step's mean, drawn over that step) and energy. */
export function chartData(state: DashboardState): ChartData<'line', Array<{ x: number; y: number }>> {
  return {
    datasets: RUNS.flatMap(([run, label]) => [
      {
        label: `${label} kW`,
        data: state.power[run].map((p) => ({ x: p.t, y: p.kw })),
        // A point's power is its step's mean: "before" draws it over the step that ended there.
        stepped: 'before' as const,
        borderColor: RUN_COLOR[run],
        backgroundColor: RUN_COLOR[run],
        borderWidth: 3,
        pointRadius: 0,
        yAxisID: 'kw',
      },
      {
        label: `${label} kWh`,
        data: state.power[run].map((p) => ({ x: p.t, y: p.kwh })),
        borderColor: RUN_COLOR[run],
        backgroundColor: RUN_COLOR[run],
        borderDash: [8, 6],
        borderWidth: 2,
        pointRadius: 0,
        yAxisID: 'kwh',
      },
    ]),
  };
}

export function EnergyChart({ state, video }: { state: DashboardState; video: boolean }) {
  const axis = dayWindow(state);
  const populated = state.power.spotcool.length > 0 || state.power.baseline.length > 0;
  const size = video ? 22 : 12;
  const options: ChartOptions<'line'> = {
    animation: false,
    responsive: true,
    maintainAspectRatio: false,
    parsing: false,
    normalized: true,
    interaction: { mode: 'nearest', axis: 'x', intersect: false },
    plugins: {
      legend: { labels: { color: '#e6edf5', font: { size: video ? 18 : 12 }, boxWidth: video ? 22 : 14, boxHeight: 3 } },
      tooltip: { callbacks: { title: (items) => (items[0] ? clockText(items[0].parsed.x ?? 0) : '') } },
    },
    scales: {
      x: {
        type: 'linear',
        min: axis?.[0],
        max: axis?.[1],
        ticks: { stepSize: video ? 7200 : 3600, color: '#c9d4e0', font: { size }, callback: (v) => clockText(Number(v)) },
        grid: { color: 'rgba(255,255,255,0.08)' },
      },
      kw: {
        position: 'left',
        beginAtZero: true,
        title: { display: true, text: 'kW', color: '#c9d4e0', font: { size } },
        ticks: { color: '#c9d4e0', font: { size } },
        grid: { color: 'rgba(255,255,255,0.08)' },
      },
      kwh: {
        position: 'right',
        beginAtZero: true,
        title: { display: true, text: 'kWh', color: '#c9d4e0', font: { size } },
        ticks: { color: '#c9d4e0', font: { size } },
        grid: { display: false },
      },
    },
  };
  return (
    <Panel id="energy" title="HVAC power and energy" populated={populated}>
      {!populated ? (
        <Empty>Waiting for the plants' first measurements.</Empty>
      ) : (
        <div className="energy">
          <dl className="readouts">
            {RUNS.map(([run, label]) => {
              const m = state.latest[run];
              return (
                <div key={run} className={`readout readout-${run}`}>
                  <dt>{label}</dt>
                  <dd>
                    <span className="big">{fixed(m?.totalKw, 1)}</span> kW
                  </dd>
                  <dd>
                    <span className="big">{fixed(m?.energyKwh, 1)}</span> kWh
                  </dd>
                </div>
              );
            })}
          </dl>
          <div
            className="chart"
            role="img"
            aria-label={`Power and energy: SpotCool ${fixed(state.latest.spotcool?.energyKwh, 1)} kWh, baseline ${fixed(state.latest.baseline?.energyKwh, 1)} kWh so far`}
          >
            <Line data={chartData(state)} options={options} />
          </div>
        </div>
      )}
    </Panel>
  );
}
