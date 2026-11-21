/// <reference types="vite/client" />
import type { SimpleCadApi } from '../shared/ipc-types'

declare global {
  interface Window {
    simplecad: SimpleCadApi
  }
}
