import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { DashboardState } from '../state/reducer';
import { initialState } from '../state/reducer';
import type { KpiData } from '../wire';
import { BOPTEST_CAPTION, KpiCards, STUB_KPI_NOTE } from './KpiCards';

const kpi = (run: 'spotcool' | 'baseline', provisional: boolean): KpiData => ({
  run,
  boptest: provisional ? {} : { ener_tot: 0.07, tdis_tot: run === 'spotcool' ? 10.3 : 0.15, idis_tot: 30.5 },
  comfort_where_people_kh: run === 'spotcool' ? 1.37 : 0.33,
  energy_saved_pct: run === 'spotcool' ? 16.87 : null,
  provisional,
});
const state = (provisional: boolean): DashboardState => ({
  ...initialState,
  kpi: { spotcool: { data: kpi('spotcool', provisional), t: 1 }, baseline: { data: kpi('baseline', provisional), t: 1 } },
});
// React escapes the apostrophe in the caption.
const html = (s: DashboardState, video: boolean, stub = false) =>
  renderToStaticMarkup(<KpiCards kpi={s.kpi} stub={stub} video={video} />).replaceAll('&#x27;', "'");

describe('KPI cards (the lead amendment on BOPTEST discomfort)', () => {
  it('video mode shows energy saved and comfort where people were, never tdis_tot or idis_tot', () => {
    const out = html(state(false), true);
    expect(out).toContain('16.9 %');
    expect(out).toContain('1.37');
    expect(out).toContain('0.33');
    expect(out).not.toMatch(/tdis_tot|idis_tot|BOPTEST/);
  });

  it("operator view groups BOPTEST's own KPIs under their caption when final", () => {
    const out = html(state(false), false);
    expect(out).toContain("BOPTEST's own KPIs");
    expect(out).toContain(BOPTEST_CAPTION);
    for (const name of ['ener_tot', 'tdis_tot', 'idis_tot']) expect(out).toContain(name);
  });

  it('tags energy saved provisional while the run is in progress', () => {
    expect(html(state(true), true)).toContain('provisional');
    expect(html(state(true), false)).toContain('Available when the run ends.');
  });
});

describe('KPI cards on a stub run', () => {
  it('say the KPIs are an upper bound with perfect perception, in both modes', () => {
    for (const video of [true, false]) {
      expect(html(state(false), video, true)).toContain(STUB_KPI_NOTE);
      expect(html(state(false), video, false)).not.toContain('Stub agent');
    }
    expect(STUB_KPI_NOTE).toBe('Stub agent: upper bound with perfect perception');
  });
});
