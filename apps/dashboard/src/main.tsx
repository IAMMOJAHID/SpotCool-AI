import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { readConfig } from './config';
import './styles.css';

const config = readConfig(window.location.search, window.location, {
  agentUrl: import.meta.env.VITE_AGENT_URL as string | undefined,
  dev: import.meta.env.DEV,
});
document.documentElement.classList.toggle('video-mode', config.video);

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App config={config} />
    </StrictMode>,
  );
}
