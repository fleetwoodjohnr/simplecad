import { CircleCheck, Info, TriangleExclamationPoint } from 'lucide-react'
import type { Finding } from './printability'
import { Btn } from '../ui'

const icons = {
  ok: <CircleCheck className="size-4 shrink-0 text-success" aria-label="OK" />,
  note: <Info className="size-4 shrink-0 text-text-2" aria-label="Note" />,
  warning: <TriangleExclamationPoint className="size-4 shrink-0 text-warn" aria-label="Warning" />
}

/** What the slicer will meet, as SimpleCAD 0.4's print check reports it, for the bodies shown. */
export function PrintCheckPanel({ findings, busy, onPlate, onClose }: {
  findings: Finding[]; busy: boolean; onPlate: (how: 'drop' | 'center') => void; onClose: () => void
}) {
  return <section aria-label="Print check" className="grid shrink-0 gap-2 text-xs">
    <div className="font-semibold">Print check</div>
    <ul aria-label="Findings" className="grid gap-1.5">
      {findings.map((finding, index) => <li key={index} className="flex gap-2">
        {icons[finding.severity]}
        <div className="min-w-0"><div>{finding.message}</div>{finding.hint && <div className="text-text-2">{finding.hint}</div>}</div>
      </li>)}
    </ul>
    <div className="flex flex-wrap gap-1">
      <Btn size="sm" type="button" disabled={busy} title="Sit everything shown on the plate (z = 0)" onClick={() => onPlate('drop')}>Drop all</Btn>
      <Btn size="sm" type="button" disabled={busy} title="Centre everything shown on the build plate" onClick={() => onPlate('center')}>Center all</Btn>
      <Btn size="sm" type="button" onClick={onClose}>Close</Btn>
    </div>
  </section>
}
