import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/components/auth-provider";
import { TopNav } from "@/components/top-nav";

export const metadata: Metadata = {
  title: "Hernán — Portfolio Prediction Engine",
  description:
    "Lagged-regression predictions across 9 stocks with macro/market signals, " +
    "live broker reconciliation, and out-of-sample backtests.",
  openGraph: {
    title: "Hernán — Portfolio Prediction Engine",
    description: "Daily quant predictions, model diagnostics, and live positions.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="antialiased">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=DM+Sans:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <AuthProvider>
          <div className="min-h-screen flex flex-col">
            <TopNav />
            <main className="flex-1">{children}</main>
          </div>
        </AuthProvider>
      </body>
    </html>
  );
}
