export interface AuthUser {
  user_id: string;
  email: string;
  merchant_id: string;
  role: "shopper" | "support_agent" | "manager" | "admin" | "service";
  display_name?: string;
}

export interface WsMessage {
  type: string;
  [key: string]: unknown;
}
