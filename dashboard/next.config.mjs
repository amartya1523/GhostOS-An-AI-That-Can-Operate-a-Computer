/** Static export: `ghost serve` hosts the built dashboard on the same origin as the API. */
const nextConfig = {
  output: "export",
  reactStrictMode: true,
  images: { unoptimized: true },
};

export default nextConfig;
