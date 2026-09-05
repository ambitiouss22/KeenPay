import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KeenPay — Secure AI Payments",
  description:
    "AI agents shop on a customer's behalf. Only the Control Plane moves money, and every money action is bounded, gated and written to an audit trail.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans">{children}</body>
    </html>
  );
}
