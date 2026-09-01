"use client";

interface TraceEvent {
  event_id: string;
  event_type: string;
  node_name?: string;
  payload?: Record<string, unknown>;
}

interface TracePanelProps {
  events: TraceEvent[];
}

export function TracePanel({ events }: TracePanelProps) {
  return (
    <div style={{ padding: 16, height: "100%", borderLeft: "1px solid #ddd" }}>
      <h2>Live Trace</h2>
      <p style={{ color: "#666", fontSize: 14 }}>Guardrail and graph events stream here</p>
      <ul style={{ listStyle: "none", padding: 0, fontFamily: "monospace", fontSize: 13 }}>
        {events.length === 0 && <li style={{ color: "#999" }}>No events yet — send a chat message</li>}
        {events.map((e) => (
          <li key={e.event_id} style={{ marginBottom: 8, padding: 8, background: "#f5f5f5", borderRadius: 4 }}>
            <div>
              <strong>{e.event_type}</strong>
              {e.node_name && <span> · {e.node_name}</span>}
            </div>
            {e.payload && Object.keys(e.payload).length > 0 && (
              <pre style={{ margin: "4px 0 0", whiteSpace: "pre-wrap" }}>
                {JSON.stringify(e.payload, null, 2)}
              </pre>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
