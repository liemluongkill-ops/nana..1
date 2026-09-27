import { defineConfig } from "vite";

function avatarHealthMiddleware() {
  const attach = (server) => {
    server.middlewares.use("/avatar-health", async (_request, response) => {
      let ok = false;
      try {
        const upstream = await fetch("http://127.0.0.1:8766/healthz", {
          signal: AbortSignal.timeout(800),
        });
        ok = upstream.ok;
      } catch {
        ok = false;
      }
      response.statusCode = 200;
      response.setHeader("Content-Type", "application/json");
      response.setHeader("Cache-Control", "no-store");
      response.end(JSON.stringify({ ok }));
    });
  };
  return {
    name: "nana-avatar-health",
    configureServer: attach,
    configurePreviewServer: attach,
  };
}

const avatarProxy = {
  target: "http://127.0.0.1:8766",
  changeOrigin: true,
  rewrite: (path) => path.replace(/^\/avatar-api/, ""),
};

export default defineConfig({
  plugins: [avatarHealthMiddleware()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    watch: { ignored: ["**/public/unity-fidelity/**", "**/public/unity/**"] },
    proxy: {
      "/avatar-api": avatarProxy,
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/avatar-api": avatarProxy,
    },
  },
  build: {
    target: "es2022",
  },
});
