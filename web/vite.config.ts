import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Reads the repo-root .env (shared with the Python API) for VITE_* vars.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "VITE_");
  return {
    plugins: [react()],
    envDir: "..",
    server: {
      host: env.VITE_HOST || "127.0.0.1",
      port: Number(env.VITE_PORT || 5173),
    },
  };
});
