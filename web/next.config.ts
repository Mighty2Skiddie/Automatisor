import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Emits a self-contained server bundle so the Docker runtime stage needs neither
  // node_modules nor the build toolchain.
  output: "standalone",
  // The browser calls the FastAPI service directly; this is only used for the
  // server-side default when NEXT_PUBLIC_API_URL is unset.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
    NEXT_PUBLIC_LANGFUSE_HOST:
      process.env.NEXT_PUBLIC_LANGFUSE_HOST ?? "https://cloud.langfuse.com",
  },
};

export default nextConfig;
