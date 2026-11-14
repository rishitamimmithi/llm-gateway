import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server forwards API calls to the gateway, so the browser sees one origin (no CORS setup needed).
const gateway = process.env.GATEWAY_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/stats": gateway,
      "/health": gateway,
    },
  },
});
