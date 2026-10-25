import { defineConfig } from "vite";
export default defineConfig({
  server: {
    proxy: {
      "/api": {
        target: process.env.IZGE_API_URL || "http://127.0.0.1:8000",
        // Preserve the browser Host so the local operator's same-origin guard also works in dev.
        changeOrigin: false,
      },
    },
  },
  // Keep CSS readable and avoid requiring a platform-native Lightning CSS binding.
  build: { cssMinify: false },
});
