/** @type {import('next').NextConfig} */
const nextConfig = {
  // Required for docker/Dockerfile.frontend, which copies .next/standalone
  // into the production image instead of shipping node_modules wholesale.
  output: 'standalone',
};

export default nextConfig;
