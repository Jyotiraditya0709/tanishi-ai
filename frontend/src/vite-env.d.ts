/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set to `ws` to stream chat over `/ws` v2 envelopes; default REST `/chat`. */
  readonly VITE_CHAT_TRANSPORT?: string;
  /** Absolute origin of Tanishi API (e.g. http://127.0.0.1:8888). Empty = same origin (or Vite proxy in dev). */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
