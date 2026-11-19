// Reads the bodies out of a `.scad3` file, the Python app's project format (simplecad/core/project.py): a zip
// holding model.json and each body's final B-Rep. Read-only: the parametric history is not ported yet, so the
// file's bodies are what the session starts from, and the file itself is never written.

import { createHash } from 'node:crypto'

/** The newest format this build reads; the Python app refuses newer files the same way. */
export const FORMAT_VERSION = 4

export interface Entry { name: string; entry: string; sha?: string }

interface SavedBody { id?: unknown; name?: unknown; visible?: unknown }

/** The zip entry for a body in a format 1 file, as the Python app's `_safe_entry` names it. */
export function legacyEntry(name: string): string {
  const keep = [...name].map((c) => /[\p{L}\p{N}_.-]/u.test(c) ? c : '_').join('')
  return `bodies/${keep || 'body'}.brep`
}

/** Which archive entries hold the shown bodies, and what is left out. */
export function bodyEntries(model: unknown): { entries: Entry[]; notes: string[] } {
  const m = (model && typeof model === 'object' ? model : {}) as Record<string, unknown>
  const format = Number(m.format ?? 1)
  if (!Number.isInteger(format) || format < 1) throw new Error('This project could not be read. The file may be damaged.')
  if (format > FORMAT_VERSION) throw new Error('This project was saved by a newer SimpleCAD. Update SimpleCAD to open it.')
  const all = (Array.isArray(m.bodies) ? m.bodies : Object.values(m.bodies ?? {})) as SavedBody[]
  const bodies = all.filter((b): b is SavedBody & { name: string } => !!b && typeof b.name === 'string')
  const shown = bodies.filter((b) => b.visible !== false)
  const notes: string[] = []
  const hidden = bodies.length - shown.length
  if (hidden) notes.push(`${hidden} hidden ${hidden === 1 ? 'body is' : 'bodies are'} not shown.`)

  const entries: Entry[] = []
  if (format >= 2) {
    const geometry = (m.geometry ?? {}) as Record<string, { sha256?: unknown } | undefined>
    for (const body of shown) {
      const sha = typeof body.id === 'string' ? geometry[body.id]?.sha256 : undefined
      if (typeof sha === 'string' && /^[0-9a-f]{64}$/.test(sha)) entries.push({ name: body.name, entry: `geometry/${sha}.brep`, sha })
      else notes.push(`"${body.name}" has no saved geometry; open it in SimpleCAD 0.4 to rebuild it.`)
    }
  } else {
    // Two bodies whose names map to the same entry can't be told apart, so neither is read.
    const count = new Map<string, number>()
    for (const body of bodies) count.set(legacyEntry(body.name), (count.get(legacyEntry(body.name)) ?? 0) + 1)
    for (const body of shown) {
      const entry = legacyEntry(body.name)
      if (count.get(entry) === 1) entries.push({ name: body.name, entry })
      else notes.push(`"${body.name}" shares its saved name with another body; open it in SimpleCAD 0.4 to rebuild it.`)
    }
  }
  return { entries, notes }
}

export interface Archive {
  readArchive(data: Buffer, names: string[]): (Buffer | null)[]
}

/** The shown bodies' B-Reps, in the file's body order. A damaged entry is left out with a note, as in the Python app. */
export function readProject(archive: Archive, data: Buffer): { bodies: { name: string; brep: Buffer }[]; notes: string[] } {
  const [raw] = archive.readArchive(data, ['model.json'])
  if (!raw) throw new Error('This file has no SimpleCAD model in it. The file may be damaged.')
  let model: unknown
  try {
    model = JSON.parse(raw.toString('utf8'))
  } catch {
    throw new Error('This project could not be read. The file may be damaged.')
  }
  const { entries, notes } = bodyEntries(model)
  const blobs = archive.readArchive(data, entries.map((e) => e.entry))
  const bodies: { name: string; brep: Buffer }[] = []
  entries.forEach((entry, i) => {
    const blob = blobs[i]
    if (!blob || (entry.sha && createHash('sha256').update(blob).digest('hex') !== entry.sha)) {
      notes.push(`"${entry.name}" could not be read from this file.`)
    } else {
      bodies.push({ name: entry.name, brep: blob })
    }
  })
  if (!bodies.length) throw new Error('This project has no saved bodies this build can show. Open it in SimpleCAD 0.4.')
  return { bodies, notes }
}
