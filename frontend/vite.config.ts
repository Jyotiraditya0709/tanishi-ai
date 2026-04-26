import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const proxyTarget = process.env.VITE_PROXY_API ?? "http://127.0.0.1:8888";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../tanishi/dashboard_v2",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/status": { target: proxyTarget, changeOrigin: true },
      "/health": { target: proxyTarget, changeOrigin: true },
      "/chat": { target: proxyTarget, changeOrigin: true },
      "/memory": { target: proxyTarget, changeOrigin: true },
      "/tasks": { target: proxyTarget, changeOrigin: true },
      "/notifications/read": { target: proxyTarget, changeOrigin: true },
      "/notifications": { target: proxyTarget, changeOrigin: true },
      "/screenshot": { target: proxyTarget, changeOrigin: true },
      "/ws": { target: proxyTarget, changeOrigin: true, ws: true },
    },
  },
});
