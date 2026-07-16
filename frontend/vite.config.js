import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // El front pide /api/... y Vite lo reenvía al backend de FastAPI.
      "/api": "http://localhost:8000",
    },
  },
});
