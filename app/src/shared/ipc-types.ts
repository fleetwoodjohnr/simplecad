/** What preload exposes to the page as `window.simplecad`. */
export interface SimpleCadApi {
  platform: string
  openProject(): Promise<{ name: string; data: Uint8Array } | undefined>
  /** Writes to the session's file, or asks where; resolves to the file's name, or nothing if cancelled. */
  saveProject(data: Uint8Array, saveAs: boolean): Promise<string | undefined>
  confirmDiscard(): Promise<boolean>
  /** The session no longer has a file: the next Save asks where. */
  newFile(): Promise<void>
  checkForUpdates(): Promise<void>
  windowMinimize(): Promise<void>
  windowToggleMaximize(): Promise<void>
  windowClose(): Promise<void>
}
