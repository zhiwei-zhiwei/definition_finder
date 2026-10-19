/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Docker: emits .next/standalone (server.js + only the node_modules it uses)
  output: "standalone",
  webpack: (config) => {
    config.resolve.alias.canvas = false;
    return config;
  },
};

export default nextConfig;
