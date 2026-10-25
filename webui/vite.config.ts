import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    proxy: {
      // 开发后端 qq_agent_harness 只监听 127.0.0.1，避免 localhost 在 Windows 上先解析到 ::1。
      "/api": "http://127.0.0.1:6056",
    },
  },
  build: {
    // 得意黑按 unicode-range 拆成几十个小字体文件，浏览器只按需加载。
    assetsInlineLimit: 0,
    // CodeMirror（含 Markdown 内嵌语言）单独成包且只在配置文本页按需加载。
    chunkSizeWarningLimit: 600,
  },
});
