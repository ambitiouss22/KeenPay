import { apiFetch } from "./api-client";

/**
 * Call the Control Plane through the protocol gateway, exactly as an external
 * agent would: an MCP tools/call in, a canonical result out. The gateway only
 * exposes non-money-moving actions, so this is the whole surface a UI needs to
 * drive the AI buyer.
 */
export interface GatewayReply<T> {
  protocol: string;
  reply: {
    result?: { action: string; data: T };
    error?: { action: string; message: string; data?: unknown };
  };
}

export interface GatewayResult<T> {
  ok: boolean;
  data?: T;
  error?: string;
}

export async function mcp<T = Record<string, unknown>>(
  name: string,
  args: Record<string, unknown> = {},
): Promise<GatewayResult<T>> {
  try {
    const body = JSON.stringify({
      message: { method: "tools/call", params: { name, arguments: args } },
    });
    const res = await apiFetch<GatewayReply<T>>("/api/v1/protocol/mcp", {
      method: "POST",
      body,
    });
    if (res.reply.result) return { ok: true, data: res.reply.result.data };
    return { ok: false, error: res.reply.error?.message ?? "gateway error" };
  } catch (e) {
    // A money-moving verb is refused with 403 before any delegate runs, and
    // apiFetch turns that into a thrown error carrying the reason.
    return { ok: false, error: e instanceof Error ? e.message : "request failed" };
  }
}
