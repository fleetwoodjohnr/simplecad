import { useState } from 'react'
import type { Preferences } from '@/shared/preferences'
import { Btn, Modal, inputCls } from '../ui'
import { version } from '../../../package.json'

export function PreferencesDialog({ current, onApply, onClose }: {
  current: Preferences
  onApply: (preferences: Preferences) => void
  onClose: () => void
}) {
  const [next, setNext] = useState(current)
  const set = (patch: Partial<Preferences>) => setNext((previous) => ({ ...previous, ...patch }))
  return <Modal title="Settings" open onOpenChange={(open) => { if (!open) onClose() }}>
    <form className="grid gap-3 text-sm" onSubmit={(event) => { event.preventDefault(); onApply(next) }}>
      <label className="grid gap-1">Theme
        <select aria-label="Theme" className={inputCls} value={next.theme}
          onChange={(event) => set({ theme: event.target.value as Preferences['theme'] })}>
          <option value="system">Follow system</option><option value="light">Light</option><option value="dark">Dark</option>
        </select>
      </label>
      <label className="grid gap-1">Display units
        <select aria-label="Display units" className={inputCls} value={next.displayUnit}
          onChange={(event) => set({ displayUnit: event.target.value as Preferences['displayUnit'] })}>
          <option value="mm">Millimetres</option><option value="cm">Centimetres</option>
          <option value="m">Metres</option><option value="in">Inches</option>
        </select>
      </label>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={next.snap} onChange={(event) => set({ snap: event.target.checked })} />
        Snap when dragging
      </label>
      <label className="grid gap-1">Move increment (mm)
        <input type="number" min="0.01" max="100" step="any" className={inputCls} required value={next.moveStep}
          onChange={(event) => set({ moveStep: Number(event.target.value) })} />
      </label>
      <label className="grid gap-1">Rotation increment (°)
        <input type="number" min="1" max="180" step="any" className={inputCls} required value={next.rotationSnap}
          onChange={(event) => set({ rotationSnap: Number(event.target.value) })} />
      </label>
      <fieldset className="grid gap-1.5 border-t border-border pt-3">
        <legend className="pb-1 font-medium">Updates</legend>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={next.updateCheck} onChange={(event) => set({ updateCheck: event.target.checked })} />
          Check for updates automatically
        </label>
        <div className="flex items-center justify-between gap-2 text-text-2">
          <span>Version {version}</span>
          <Btn type="button" size="sm" onClick={() => void window.simplecad.checkForUpdates()}>Check now</Btn>
        </div>
      </fieldset>
      <div className="flex justify-end gap-2">
        <Btn type="button" onClick={onClose}>Cancel</Btn>
        <Btn type="submit" variant="primary">Apply</Btn>
      </div>
    </form>
  </Modal>
}
