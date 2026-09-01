import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "KeenPay",
  description: "Agentic checkout with policy-gated payments",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
