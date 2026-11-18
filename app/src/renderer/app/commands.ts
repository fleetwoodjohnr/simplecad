// The suite's command registry (app/commands.ts in Simple Viz) arrives with the tools; for now, only how a
// shortcut is written, which the UI kit's tooltips and menus use.

export const isMac = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform)

export function formatShortcut(s: string | undefined): string {
  if (!s) return ''
  return s
    .split(' ')
    .map((chord) =>
      chord
        .split('-')
        .map((k) => {
          if (k === 'Mod') return isMac ? '⌘' : 'Ctrl'
          if (k === 'Shift') return isMac ? '⇧' : 'Shift'
          if (k === 'Alt') return isMac ? '⌥' : 'Alt'
          if (k === 'Ctrl') return isMac ? '⌃' : 'Ctrl'
          if (k === 'Enter') return isMac ? '↩' : 'Enter'
          if (k === 'Space') return 'Space'
          if (k === 'Minus') return '-'
          if (k === 'Plus') return '+'
          return k.length === 1 ? k.toUpperCase() : k
        })
        .join(isMac ? '' : '+')
    )
    .join(', ')
}

export const commands = {
  newBox: { label: 'New box', shortcut: 'Mod-N' },
  open: { label: 'Open project', shortcut: 'Mod-O' },
  save: { label: 'Save As', shortcut: 'Mod-S' },
  undo: { label: 'Undo', shortcut: 'Mod-Z' },
  redo: { label: 'Redo', shortcut: 'Mod-Shift-Z' },
  fit: { label: 'Fit view', shortcut: 'F' },
  clear: { label: 'Clear selection' },
  example: { label: 'Open recessed wall example' },
  updates: { label: 'Check for updates' }
} satisfies Record<string, { label: string; shortcut?: string }>

export type CommandId = keyof typeof commands
export type CommandActions = Record<CommandId, { enabled: boolean; run: () => void }>

export function matchesShortcut(event: KeyboardEvent, shortcut: string): boolean {
  const keys = shortcut.split('-')
  const key = keys.at(-1)
  const modifier = isMac ? event.metaKey : event.ctrlKey
  const other = isMac ? event.ctrlKey : event.metaKey
  return key?.toLowerCase() === event.key.toLowerCase() &&
    !other && !event.altKey && modifier === keys.includes('Mod') && event.shiftKey === keys.includes('Shift')
}
