import { useMemo, useReducer } from 'react';
import { useCameraCatalog } from './cameras';
import type { Config } from './config';
import { useEventFeed } from './feed';
import { AcUnit } from './panels/AcUnit';
import { AgentPanel } from './panels/AgentPanel';
import { BacnetLog } from './panels/BacnetLog';
import { CameraWall } from './panels/CameraWall';
import { DevPanels } from './panels/DevPanels';
import { EnergyChart } from './panels/EnergyChart';
import { FloorPlan } from './panels/FloorPlan';
import { Header } from './panels/Header';
import { KpiCards } from './panels/KpiCards';
import { Timeline } from './panels/Timeline';
import { initialState, reducer } from './state/reducer';
import { dayWindow } from './state/selectors';
import { PanelBoundary } from './ui';

// Each panel gets only its slice of the state and is memoized, so an event re-draws only the
// panels it changes (the VRF unit's 5 Hz state re-draws the AC unit alone).
export function App({ config }: { config: Config }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  useEventFeed(config.eventsUrl, dispatch);
  const { catalog, error } = useCameraCatalog(config.camerasUrl, state.run?.id ?? null);
  const axis = useMemo(() => dayWindow(state.simNow, state.power), [state.simNow, state.power]);
  const { video, panels } = config;
  const epoch = state.epoch;
  // RC is our own test model: none of its figures is ever quoted (docs/release/numbers.md).
  const rc = state.run?.plant === 'rc';
  const shown = (id: string) => panels === null || panels.includes(id);
  // A chosen few fill the window, in two columns at most (styles.css: an odd last one spans both).
  // Three, as in the quad take's floor plan, energy and feed: the top two get the taller row.
  const count = Math.max(panels?.filter((id) => id !== 'header').length ?? 0, 1);
  const grid =
    panels === null
      ? undefined
      : {
          gridTemplateColumns: `repeat(${Math.min(count, 2)}, minmax(0, 1fr))`,
          gridTemplateRows: count === 3 ? 'minmax(0, 3fr) minmax(0, 2fr)' : `repeat(${Math.ceil(count / 2)}, minmax(0, 1fr))`,
        };
  return (
    <div className={`app ${video ? 'video' : 'operator'}${panels === null ? '' : ' panels'}`}>
      {shown('header') && (
        <PanelBoundary key={`header-${epoch}`} id="header" title="Header">
          <Header
            run={state.run}
            simNow={state.simNow}
            connection={state.connection}
            attempt={state.attempt}
            stub={state.stubAgent}
            config={config}
          />
        </PanelBoundary>
      )}
      <main className="grid" style={grid}>
        {shown('floorplan') && (
          <PanelBoundary key={`floorplan-${epoch}`} id="floorplan" title="Floor plan">
            <FloorPlan measured={state.latest.spotcool} script={state.script} people={state.people} video={video} />
          </PanelBoundary>
        )}
        {shown('cameras') && (
          <PanelBoundary key={`cameras-${epoch}`} id="cameras" title="Cameras">
            <CameraWall
              catalog={catalog}
              error={error}
              camerasUrl={config.camerasUrl}
              runId={state.run?.id ?? null}
              people={state.people}
              masks={state.masks}
              scenes={state.scenes}
              script={state.script}
              video={video}
              showMasks={config.masks}
              privacy={config.privacy ? { agentUrl: config.agentUrl } : null}
            />
          </PanelBoundary>
        )}
        {shown('kpis') && (
          <PanelBoundary key={`kpis-${epoch}`} id="kpis" title="Results">
            <KpiCards kpi={state.kpi} stub={state.stubAgent} video={video} rc={rc} />
          </PanelBoundary>
        )}
        {shown('energy') && (
          <PanelBoundary key={`energy-${epoch}`} id="energy" title="HVAC power and energy">
            <EnergyChart power={state.power} latest={state.latest} axis={axis} video={video} rc={rc} />
          </PanelBoundary>
        )}
        {shown('timeline') && (
          <PanelBoundary key={`timeline-${epoch}`} id="timeline" title="Timeline">
            <Timeline
              simNow={state.simNow}
              axis={axis}
              doors={state.doors}
              calendar={state.calendar}
              complaints={state.complaints}
              video={video}
            />
          </PanelBoundary>
        )}
        {shown('bacnet') && (
          <PanelBoundary key={`bacnet-${epoch}`} id="bacnet" title="BACnet writes">
            <BacnetLog rows={state.bacnet} video={video} />
          </PanelBoundary>
        )}
        {shown('acunit') && (
          <PanelBoundary key={`acunit-${epoch}`} id="acunit" title="Meeting-room AC unit">
            <AcUnit vrf={state.vrf} />
          </PanelBoundary>
        )}
        {shown('agent') && (
          <PanelBoundary key={`agent-${epoch}`} id="agent" title="Agent">
            <AgentPanel items={state.agent} stub={state.stubAgent} config={config} />
          </PanelBoundary>
        )}
        {!video && shown('errors') && (
          <PanelBoundary key={`dev-${epoch}`} id="errors" title="World errors">
            <DevPanels errors={state.errors} metrics={state.metrics} dropped={state.dropped} />
          </PanelBoundary>
        )}
      </main>
    </div>
  );
}
