import { defineConfig } from "vite";
export default defineConfig({
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  // Keep CSS readable and avoid requiring a platform-native Lightning CSS binding.
  build: { cssMinify: false },
});
