import type { Metadata } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages } from "next-intl/server";
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

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Locale comes from the NEXT_LOCALE cookie (default 'es'); see i18n/request.ts.
  const locale = await getLocale();
  const messages = await getMessages();

  return (
    <html lang={locale} className="antialiased">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=DM+Sans:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <NextIntlClientProvider locale={locale} messages={messages}>
          <AuthProvider>
            <div className="min-h-screen flex flex-col">
              <TopNav />
              <main className="flex-1">{children}</main>
            </div>
          </AuthProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
