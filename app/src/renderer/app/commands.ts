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
  save: { label: 'Save', shortcut: 'Mod-S' },
  saveAs: { label: 'Save As', shortcut: 'Mod-Shift-S' },
  undo: { label: 'Undo', shortcut: 'Mod-Z' },
  redo: { label: 'Redo', shortcut: 'Mod-Shift-Z' },
  fit: { label: 'Fit view', shortcut: 'F' },
  clear: { label: 'Clear selection' },
  shape: { label: 'Shape' },
  pushPull: { label: 'Push/Pull' },
  stack: { label: 'Stack & Place' },
  arrange: { label: 'Arrange' },
  measure: { label: 'Measure' },
  printCheck: { label: 'Print check' },
  fitClearance: { label: 'Fit & Clearance' },
  move: { label: 'Move' },
  mirror: { label: 'Mirror' },
  rectangularPattern: { label: 'Rectangular Pattern' },
  circularPattern: { label: 'Circular Pattern' },
  scale: { label: 'Scale' },
  combine: { label: 'Combine' },
  split: { label: 'Split' },
  sectionReplace: { label: 'Section Replace' },
  shell: { label: 'Shell' },
  vent: { label: 'Vent' },
  fillet: { label: 'Fillet' },
  hinge: { label: 'Hinge' },
  clipJoint: { label: 'Clip Joint' },
  midplane: { label: 'Midplane' },
  planeAtAngle: { label: 'Plane at Angle' },
  planeThroughPoints: { label: 'Plane through Points' },
  tangentPlane: { label: 'Tangent Plane' },
  axis: { label: 'Axis' },
  point: { label: 'Point' },
  chamfer: { label: 'Chamfer' },
  rotate: { label: 'Rotate' },
  sketch: { label: 'Sketch' },
  offsetPlane: { label: 'Offset Plane' },
  extrude: { label: 'Extrude' },
  revolve: { label: 'Revolve' },
  sweep: { label: 'Sweep' },
  loft: { label: 'Loft' },
  path_pattern: { label: 'Pattern Along Path' },
  hole: { label: 'Hole' },
  thread: { label: 'Thread' },
  matchingPart: { label: 'Matching Part' },
  text: { label: 'Text' },
  faceTexture: { label: 'Face Texture' },
  settings: { label: 'Settings' },
  slicer: { label: 'Send to slicer' },
  updates: { label: 'Check for updates' }
} satisfies Record<string, { label: string; shortcut?: string }>

export type CommandId = keyof typeof commands
/** `hint` says what a disabled command is waiting for. */
export type CommandActions = Record<CommandId, { enabled: boolean; run: () => void; hint?: string }>

export function matchesShortcut(event: KeyboardEvent, shortcut: string): boolean {
  const keys = shortcut.split('-')
  const key = keys.at(-1)
  const modifier = isMac ? event.metaKey : event.ctrlKey
  const other = isMac ? event.ctrlKey : event.metaKey
  return key?.toLowerCase() === event.key.toLowerCase() &&
    !other && !event.altKey && modifier === keys.includes('Mod') && event.shiftKey === keys.includes('Shift')
}
