import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

declare const process: { env: Record<string, string | undefined> };
const src = new URL("./src", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");

// Built into redbox_backend/dashboard/static, which rb-admin serves. `npm run dev` proxies /api to a running
// dashboard (RB_DASHBOARD_URL, default http://127.0.0.1:8443 with ADMIN_TLS=false).
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": src } },
  build: {
    outDir: "../redbox_backend/dashboard/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: {
    proxy: { "/api": { target: process.env.RB_DASHBOARD_URL || "http://127.0.0.1:8443", changeOrigin: true } },
  },
});
