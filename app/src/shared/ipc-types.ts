/** What preload exposes to the page as `window.simplecad`. */
export interface SimpleCadApi {
  platform: string
  openProject(): Promise<{ name: string; data: Uint8Array } | undefined>
  saveProject(data: Uint8Array, name: string): Promise<string | undefined>
  confirmDiscard(): Promise<boolean>
  checkForUpdates(): Promise<void>
  windowMinimize(): Promise<void>
  windowToggleMaximize(): Promise<void>
  windowClose(): Promise<void>
}
