"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { apiFetch } from "@/lib/api-client";

export default function HomePage() {
  const { user, loading, login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("shopper@keenpay.dev");
  const [password, setPassword] = useState("KeenPayDev1!");
  const [error, setError] = useState("");

  const startSession = async () => {
    setError("");
    try {
      if (!user) await login(email, password);
      const session = await apiFetch<{ session_id: string }>("/api/v1/sessions", {
        method: "POST",
        body: JSON.stringify({ merchant_id: "merchant_keen" }),
      });
      router.push(`/session/${session.session_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start");
    }
  };

  return (
    <main style={{ maxWidth: 480, margin: "80px auto", padding: 24 }}>
      <h1>KeenPay</h1>
      <p>Agentic checkout with policy-gated payments</p>
      {!user && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 16 }}>
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
          />
        </div>
      )}
      {user && <p>Signed in as {user.email} ({user.role})</p>}
      {error && <p style={{ color: "red" }}>{error}</p>}
      <button onClick={startSession} disabled={loading} style={{ padding: "10px 20px", marginTop: 8 }}>
        {loading ? "Loading..." : "Start checkout session"}
      </button>
    </main>
  );
}
