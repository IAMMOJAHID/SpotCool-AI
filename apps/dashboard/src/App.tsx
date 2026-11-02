import { useReducer } from 'react';
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

export function App({ config }: { config: Config }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  useEventFeed(config.eventsUrl, dispatch);
  const { catalog, error } = useCameraCatalog(config.camerasUrl, state.run?.id ?? null);
  const { video } = config;
  return (
    <div className={video ? 'app video' : 'app operator'}>
      <Header state={state} config={config} />
      <main className="grid">
        <FloorPlan state={state} video={video} />
        <CameraWall state={state} catalog={catalog} error={error} camerasUrl={config.camerasUrl} video={video} />
        <KpiCards state={state} video={video} />
        <EnergyChart state={state} video={video} />
        <Timeline state={state} video={video} />
        <BacnetLog state={state} video={video} />
        <AcUnit state={state} />
        <AgentPanel state={state} config={config} />
        {!video && <DevPanels state={state} />}
      </main>
    </div>
  );
}
