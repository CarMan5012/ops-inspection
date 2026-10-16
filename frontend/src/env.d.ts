/// <reference types="vite/client" />

declare global {
  interface Window {
    APP_CONFIG?: { apiBase?: string; frontendBase?: string; artifactBase?: string }
  }
}

export {}
