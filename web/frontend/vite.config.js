import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend runs on :8000. Proxying /api keeps the frontend same-origin in
// dev, which also means the eventual "remote" deploy needs no code changes —
// just point this proxy (or a reverse proxy) at the backend host.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
