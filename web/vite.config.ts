import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server runs on 5173. The FastAPI backend runs on 8000.
// Proxy /api (HTTP) and /api/pty (WebSocket) through to the backend during
// development. In production the backend serves the built SPA from the same
// origin, so the proxy is dev-only.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
