import { Component } from 'react';
import type { ReactNode } from 'react';

/** One dashboard panel: a labelled section whose `data-populated` says it has data to show. */
export function Panel(props: {
  id: string;
  title: string;
  populated: boolean;
  tags?: ReactNode;
  children: ReactNode;
}) {
  const heading = `${props.id}-title`;
  return (
    <section
      className={`panel panel-${props.id}`}
      aria-labelledby={heading}
      data-testid={`panel-${props.id}`}
      data-populated={props.populated}
    >
      <header className="panel-head">
        <h2 id={heading}>{props.title}</h2>
        {props.tags}
      </header>
      <div className="panel-body">{props.children}</div>
    </section>
  );
}

export type TagKind = 'stub' | 'provisional' | 'final' | 'script' | 'live' | 'warn';

export function Tag({ kind, children }: { kind: TagKind; children: ReactNode }) {
  return <span className={`tag tag-${kind}`}>{children}</span>;
}

/** The label every stub agent event carries: its perception is the script's, not a camera's. */
export function StubTag() {
  return (
    <Tag kind="stub">
      <span title="Stub agent: perfect perception from the scenario script, not Person A's agent">stub</span>
    </Tag>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

/**
 * A panel that failed to render shows why, in its place, while the others carry on. Key it by
 * the state's `epoch`, so a clear screen gives it a new start.
 */
export class PanelBoundary extends Component<{ id: string; title: string; children: ReactNode }, { error: string | null }> {
  override state = { error: null as string | null };

  static getDerivedStateFromError(error: unknown): { error: string } {
    return { error: error instanceof Error ? error.message : String(error) };
  }

  override render(): ReactNode {
    if (this.state.error === null) return this.props.children;
    return (
      <Panel id={this.props.id} title={this.props.title} populated={false}>
        <p className="panel-error" role="alert" data-testid="panel-error">
          This panel failed to draw: {this.state.error}
        </p>
      </Panel>
    );
  }
}
