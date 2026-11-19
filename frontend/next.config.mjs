/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_URL || "http://localhost:8000";
const nextConfig = {
  output: "standalone",
  compress: false, // keep SSE streams unbuffered
  poweredByHeader: false,
  async rewrites() {
    // Same-origin proxy: the session cookie never leaves our domain and API keys stay on the backend.
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [{
      source: "/(.*)",
      headers: [
        { key: "X-Frame-Options", value: "DENY" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      ],
    }];
  },
};
export default nextConfig;
