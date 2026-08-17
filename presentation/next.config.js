/** @type {import('next').NextConfig} */
module.exports = {
  output: "standalone",
  // Phase 1 does not use Next's image optimizer. Keep Sharp optional-install
  // behavior out of deterministic source and container builds.
  images: { unoptimized: true },
};
