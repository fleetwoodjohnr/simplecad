import { useEffect, useState } from 'react'
import fits from '../../../../simplecad/data/fits.json'
import type { Calibration } from '@/shared/ipc-types'
import { defaultPrinter } from '@/shared/printing'
import { Btn, inputCls } from '../ui'

/** The clearances the test print sweeps (the engine's calibration::SWEEP), mm. */
const SWEEP = [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]

interface Props {
  busy: boolean
  /** Adds the calibration test print to the model. */
  onTestPrint: () => void
  onSave: (measured: Pick<Calibration, 'fits' | 'thread'>) => Promise<void>
  onClose: () => void
}

/** SimpleCAD 0.4's Fit & Clearance: what each fit means on this printer, and the values a test print measured. */
export function FitPanel({ busy, onSave, onTestPrint, onClose }: Props) {
  const [saved, setSaved] = useState<Calibration>()
  const [values, setValues] = useState<Record<string, string>>({})
  useEffect(() => {
    void window.simplecad.readCalibration().then((calibration) => {
      setSaved(calibration)
      setValues({ ...Object.fromEntries(Object.entries(calibration.fits).map(([key, value]) => [key, String(value)])), thread: String(calibration.thread) })
    })
  }, [])
  const numbers = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, Number(value)]))
  const valid = !!saved && Object.entries(values).every(([key, value]) => value.trim() !== '' && Number.isFinite(numbers[key]) &&
    numbers[key] >= (key === 'thread' ? 0 : -1) && numbers[key] <= 5)
  const field = (key: string, label: string, hint?: string) => <label key={key} className="grid gap-1" title={hint}>{label}
    <input className={inputCls} inputMode="decimal" value={values[key] ?? ''} disabled={!saved || busy}
      onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))} />
  </label>
  return (
    <form aria-label="Fit & Clearance" className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (!valid || busy) return
      const { thread, ...measured } = numbers
      void onSave({ fits: measured, thread }).then(() => setSaved((current) => current && { ...current, calibrated: true }))
    }}>
      <div className="font-semibold">Fit &amp; Clearance</div>
      <div role="status" aria-label="Calibration" className="text-text-2">
        {defaultPrinter.name} — {saved?.calibrated ? 'using your measured values.' : 'using shipped defaults. Print the calibration model to tune them.'}
      </div>
      {Object.entries(fits.fits).map(([key, fit]) => field(key, `${fit.label} (mm)`, fit.hint))}
      {field('thread', 'Normal thread clearance (mm, diametral)', fits.thread_clearance.normal.hint)}
      <div className="font-semibold">Calibration</div>
      <p className="text-text-2">Adds a pin strip and a matching plate with holes at {SWEEP[0].toFixed(2)}–{SWEEP.at(-1)!.toFixed(2)} mm
        clearance, each labelled. Print both, find the first pair that fits the way you want, and enter that number above.</p>
      <Btn type="button" size="sm" className="justify-self-start" disabled={busy} onClick={onTestPrint}>Add calibration model</Btn>
      <div className="flex gap-1">
        <Btn type="submit" size="sm" variant="primary" disabled={!valid || busy}>Save measurements</Btn>
        <Btn type="button" size="sm" onClick={onClose}>Close</Btn>
      </div>
    </form>
  )
}
