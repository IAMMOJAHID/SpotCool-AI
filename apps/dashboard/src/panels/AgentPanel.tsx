import { useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { MAX_COMPLAINT_CHARS, sendChat, sendVoice } from '../agent';
import { startRecording } from '../audio/record';
import type { Recording } from '../audio/record';
import type { Config } from '../config';
import { bmsValue, clockText } from '../format';
import type { AgentItem, DashboardState } from '../state/reducer';
import { Empty, Panel, StubTag } from '../ui';

const VIDEO_ITEMS = 2;
const SHORTEST_MS = 300;

export function AgentPanel({ state, config }: { state: DashboardState; config: Config }) {
  const items = [...state.agent].reverse();
  const shown = config.video ? items.filter((i) => i.type !== 'decision').slice(0, VIDEO_ITEMS) : items;
  return (
    <Panel id="agent" title="Agent" populated={items.length > 0} tags={state.stubAgent ? <StubTag /> : undefined}>
      {shown.length === 0 ? (
        <Empty>No decisions from the agent yet.</Empty>
      ) : (
        <ol className="feed" aria-label="The agent's decisions and explanations, newest first">
          {shown.map((item, i) => (
            <li key={`${item.tMs}-${i}`} className={`feed-${item.type}`}>
              <span className="feed-time">{item.t === null ? '' : clockText(item.t)}</span>
              <span className="feed-kind">{item.type}</span>
              <span className="feed-text">{describe(item)}</span>
              {item.stub && <StubTag />}
            </li>
          ))}
        </ol>
      )}
      <div className="talk">
        {config.agentUrl === null ? (
          <p className="no-agent" role="status">
            {config.agentProblem
              ? `Agent not configured: ${config.agentProblem}.`
              : 'No agent configured: add ?agent=http://<host>:8002 to the URL or set VITE_AGENT_URL.'}
          </p>
        ) : (
          <>
            <ChatBox agentUrl={config.agentUrl} />
            <TalkButton agentUrl={config.agentUrl} />
            {!config.video && <p className="sub">Sent only to the agent at {config.agentUrl}; nothing is stored.</p>}
          </>
        )}
      </div>
    </Panel>
  );
}

/** One line for an agent event: the stub's fields, or Person A's `text`, or the data itself. */
export function describe(item: AgentItem): string {
  const d = item.data;
  if (typeof d.text === 'string') return d.text;
  if (item.type === 'decision' && typeof d.point === 'string') {
    const value = d.value === null || typeof d.value === 'string' || typeof d.value === 'number' ? bmsValue(d.value) : '?';
    const priority = typeof d.priority === 'number' ? ` at priority ${d.priority}` : '';
    const reason = typeof d.reason === 'string' ? ` (${d.reason})` : '';
    return `${d.point} = ${value}${priority}${reason}`;
  }
  const rest = Object.fromEntries(Object.entries(d).filter(([key]) => key !== 'stub'));
  return JSON.stringify(rest).slice(0, 200);
}

function ChatBox({ agentUrl }: { agentUrl: string }) {
  const [text, setText] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const complaint = text.trim();
    if (!complaint || busy) return;
    setBusy(true);
    setStatus('Sending…');
    try {
      await sendChat(agentUrl, complaint);
      setText('');
      setStatus('Sent to the agent.');
    } catch (error) {
      setStatus(`Not sent: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className="chat"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label htmlFor="complaint">Complaint to the agent</label>
      <div className="chat-row">
        <input
          id="complaint"
          type="text"
          value={text}
          maxLength={MAX_COMPLAINT_CHARS}
          placeholder="It's too cold in here"
          onChange={(e) => {
            setText(e.target.value);
          }}
        />
        <button type="submit" disabled={busy || text.trim() === ''}>
          Send
        </button>
      </div>
      <p className="sub" role="status">
        {status}
      </p>
    </form>
  );
}

type TalkState = 'idle' | 'starting' | 'recording' | 'sending';

/** Hold to record, release to send: by pointer, or Space or Enter while the button has focus. */
function TalkButton({ agentUrl }: { agentUrl: string }) {
  const [talk, setTalk] = useState<TalkState>('idle');
  const [status, setStatus] = useState('');
  const recording = useRef<{ recording: Recording | null; held: boolean }>({ recording: null, held: false });
  // Read through a call: the press can end while `begin` awaits the microphone.
  const held = () => recording.current.held;

  const begin = async () => {
    if (talk !== 'idle') return;
    const r = recording.current;
    r.held = true;
    setTalk('starting');
    setStatus('Opening the microphone…');
    try {
      r.recording = await startRecording();
    } catch (error) {
      setTalk('idle');
      setStatus(`No microphone: ${error instanceof Error ? error.message : String(error)}`);
      return;
    }
    if (!held()) {
      cancel('Hold the button while you speak.');
      return;
    }
    setTalk('recording');
    setStatus('Recording… release to send.');
  };

  const cancel = (message: string) => {
    const r = recording.current;
    r.held = false;
    r.recording?.cancel();
    r.recording = null;
    setTalk('idle');
    setStatus(message);
  };

  const finish = async () => {
    const r = recording.current;
    r.held = false;
    const active = r.recording;
    if (!active) return; // still starting: `begin` cancels it
    if (active.elapsedMs() < SHORTEST_MS) {
      cancel('Too short: hold the button while you speak.');
      return;
    }
    r.recording = null;
    setTalk('sending');
    setStatus('Sending…');
    try {
      const wav = await active.stop();
      await sendVoice(agentUrl, wav);
      setStatus('Voice message sent to the agent.');
    } catch (error) {
      setStatus(`Not sent: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setTalk('idle');
    }
  };

  const isKey = (event: KeyboardEvent) => event.key === ' ' || event.key === 'Enter';
  return (
    <div className="talk-row">
      <button
        type="button"
        className={`talk-button talk-${talk}`}
        aria-pressed={talk === 'recording'}
        aria-describedby="talk-help"
        disabled={talk === 'sending'}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          void begin();
        }}
        onPointerUp={() => void finish()}
        onPointerCancel={() => {
          cancel('Cancelled.');
        }}
        onKeyDown={(e) => {
          if (!isKey(e)) return;
          e.preventDefault();
          if (!e.repeat) void begin();
        }}
        onKeyUp={(e) => {
          if (!isKey(e)) return;
          e.preventDefault();
          void finish();
        }}
        onBlur={() => {
          if (held()) cancel('Cancelled.');
        }}
      >
        {talk === 'recording' ? 'Recording…' : 'Hold to talk'}
      </button>
      <span id="talk-help" className="sub talk-help">
        Hold the button, or hold Space or Enter on it, while you speak.
      </span>
      <p className="sub" role="status" data-testid="talk-status">
        {status}
      </p>
    </div>
  );
}
