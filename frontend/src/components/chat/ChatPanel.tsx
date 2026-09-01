"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api-client";

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
}

interface ChatPanelProps {
  sessionId: string;
}

export function ChatPanel({ sessionId }: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const send = async () => {
    if (!input.trim()) return;
    const userMsg: Message = { id: `u-${Date.now()}`, role: "user", text: input };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setLoading(true);
    try {
      const res = await apiFetch<{
        message_id: string;
        role: string;
        text: string;
      }>(`/api/v1/sessions/${sessionId}/messages`, {
        method: "POST",
        body: JSON.stringify({ text: userMsg.text }),
      });
      setMessages((m) => [
        ...m,
        { id: res.message_id, role: "assistant", text: res.text },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: 16 }}>
      <h2>Chat</h2>
      <div style={{ flex: 1, overflowY: "auto", border: "1px solid #ddd", padding: 12, marginBottom: 12 }}>
        {messages.map((m) => (
          <div key={m.id} style={{ marginBottom: 8, textAlign: m.role === "user" ? "right" : "left" }}>
            <strong>{m.role}:</strong> {m.text}
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="2 navy hoodies, best price..."
          style={{ flex: 1, padding: 8 }}
        />
        <button onClick={send} disabled={loading}>
          {loading ? "..." : "Send"}
        </button>
      </div>
    </div>
  );
}
