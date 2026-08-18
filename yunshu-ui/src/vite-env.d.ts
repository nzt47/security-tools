/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端 API 基址（桌面版需指向本地/远程 Flask 服务，如 http://127.0.0.1:5678） */
  readonly VITE_API_BASE?: string;
  /** 设为 1 时启用 Web 模式的 Electron Mock（双标签页联调独立窗口同步） */
  readonly VITE_MOCK_ELECTRON?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
