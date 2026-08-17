const API_TARGET = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Same-origin /api/* proxied to the FastAPI backend — no CORS in the browser.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_TARGET}/api/:path*` }];
  },
};

export default nextConfig;
