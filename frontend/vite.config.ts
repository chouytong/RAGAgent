import { defineConfig } from "vite";
export default defineConfig({
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**"] },
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
});
