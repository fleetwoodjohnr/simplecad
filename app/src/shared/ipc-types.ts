import type { Preferences } from './preferences'
import type { ExportFormat, ModelImport } from './engine-protocol'

export interface OfferedProject { name: string; data: Uint8Array; token: string }

/** What preload exposes to the page as `window.simplecad`. */
export interface SimpleCadApi {
  platform: string
  openProject(): Promise<OfferedProject | undefined>
  pendingProject(): Promise<OfferedProject | undefined>
  acceptProject(token: string): Promise<void>
  onProjectRequested(run: () => void): () => void
  openModel(): Promise<ModelImport | undefined>
  /** Writes to the session's file, or asks where; resolves to the file's name, or nothing if cancelled. */
  saveProject(data: Uint8Array, saveAs: boolean): Promise<string | undefined>
  saveExport(data: Uint8Array, format: ExportFormat): Promise<string | undefined>
  confirmDiscard(): Promise<boolean>
  /** The session no longer has a file: the next Save asks where. */
  newFile(): Promise<void>
  /** How long ago a session that did not end cleanly left unsaved work, if one did. */
  pendingRecovery(): Promise<string | undefined>
  /** That work, to open as unsaved; the session then has no file. */
  recoverWork(): Promise<{ name: string; data: Uint8Array } | undefined>
  discardRecovery(): Promise<void>
  /** Closing with unsaved work, the user chose Save: save, then close. Returns the unsubscribe. */
  onSaveAndClose(run: () => void): () => void
  legacyPreferences(): Promise<Preferences | undefined>
  checkForUpdates(): Promise<void>
  windowMinimize(): Promise<void>
  windowToggleMaximize(): Promise<void>
  windowClose(): Promise<void>
}
