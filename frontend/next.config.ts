import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The frontend talks to the backend over HTTP. Set NEXT_PUBLIC_API_URL in .env.local.
};

export default nextConfig;
