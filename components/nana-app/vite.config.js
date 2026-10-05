import { defineConfig } from "vite";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  publicDir: resolve(appRoot, "public"),
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    hmr: false,
    watch: {
      ignored: ["**/Build/**", "**/StreamingAssets/**"],
    },
    fs: {
      allow: [appRoot],
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
  },
  build: {
    target: "es2022",
    rollupOptions: {
      input: {
        app: resolve(appRoot, "index.html"),
        obs: resolve(appRoot, "obs.html"),
      },
    },
  },
});
