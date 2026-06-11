import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The frontend talks to the backend over HTTP. Set NEXT_PUBLIC_API_URL in .env.local.
};

// Cookie-based locale (no i18n routing) — points the plugin at our request config.
const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

export default withNextIntl(nextConfig);
