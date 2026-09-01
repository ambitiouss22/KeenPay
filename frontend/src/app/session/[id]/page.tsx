"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { TracePanel } from "@/components/trace/TracePanel";
import { useAuth } from "@/hooks/useAuth";
import { apiFetch } from "@/lib/api-client";

export default function SessionPage({ params }: { params: { id: string } }) {
  const { user, loading, login } = useAuth();
  const router = useRouter();
  const [traceEvents, setTraceEvents] = useState<
    { event_id: string; event_type: string; node_name?: string; payload?: Record<string, unknown> }[]
  >([]);

  useEffect(() => {
    if (!loading && !user) {
      login("shopper@keenpay.dev", "KeenPayDev1!").catch(() => router.push("/"));
    }
  }, [loading, user, login, router]);

  useEffect(() => {
    if (!user) return;
    apiFetch<{ items: unknown[] }>(`/api/v1/sessions/${params.id}/audit`)
      .then(() => {})
      .catch(() => {});
  }, [user, params.id]);

  if (loading || !user) return <p style={{ padding: 24 }}>Loading session...</p>;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", height: "100vh" }}>
      <ChatPanel sessionId={params.id} />
      <TracePanel events={traceEvents} />
    </div>
  );
}
