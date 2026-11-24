import { forwardRef, memo } from 'react'
import { Pencil } from 'lucide-react'
import type { StepInfo } from '@/shared/engine-protocol'
import { Btn, IconBtn } from '../ui'

const STATES: Record<string, string> = { suppressed: 'Suppressed', failed: 'Failed', needs_attention: 'Needs attention' }

interface Props {
  steps: StepInfo[]
  busy: boolean
  /** Steps whose own panel is open, so they can't be edited again from here. */
  editing: (string | undefined)[]
  repairing?: string
  onEdit: (step: StepInfo) => void
  onRepair: (step: StepInfo) => void
  onKeep: (step: StepInfo) => void
  /** A right click, or the menu key, on a step: what can be done to it. */
  onMenu: (step: StepInfo, at: { x: number; y: number }) => void
}

/** Whether History can open the step's settings to change. */
export const canEdit = (step: StepInfo) => !step.undone && !!(step.faceSettings || step.shapeSettings || step.kind === 'sketch')
/** What keeping a step that lost an input does: a placement stays where it is, anything else keeps its shape. */
export const keepLabel = (step: StepInfo) => step.kind === 'align' ? 'Keep where it is' : 'Keep this shape'

/** The model's steps. A large project has hundreds, so each row draws again only when its step changes, and
 * the busy state disables every button through the fieldset rather than through each row. */
export const HistoryList = forwardRef<HTMLOListElement, Props>(function HistoryList({ steps, busy, editing, repairing, onEdit, onRepair, onKeep, onMenu }, ref) {
  return (
    <fieldset disabled={busy} className="contents">
      <ol ref={ref} aria-label="Model history" className="min-h-16 flex-1 overflow-auto text-xs">
        {steps.map((step) => <Row key={step.id} step={step} editing={editing.includes(step.id)}
          repairing={repairing === step.id} onEdit={onEdit} onRepair={onRepair} onKeep={onKeep} onMenu={onMenu} />)}
      </ol>
    </fieldset>
  )
})

interface RowProps {
  step: StepInfo
  editing: boolean
  repairing: boolean
  onEdit: (step: StepInfo) => void
  onRepair: (step: StepInfo) => void
  onKeep: (step: StepInfo) => void
  onMenu: (step: StepInfo, at: { x: number; y: number }) => void
}

// Every result brings new step objects, so rows compare what they show.
const Row = memo(function Row({ step, editing, repairing, onEdit, onRepair, onKeep, onMenu }: RowProps) {
  return (
    // Rows scrolled out of view skip style, layout and paint: a large project has hundreds.
    <li className={`px-2 py-1.5 [content-visibility:auto] [contain-intrinsic-size:auto_28px] ${step.undone ? 'text-text-3' : ''}`}
      onContextMenu={(event) => {
        event.preventDefault()
        const box = event.currentTarget.getBoundingClientRect()
        onMenu(step, event.clientX || event.clientY ? { x: event.clientX, y: event.clientY } : { x: box.left + 12, y: box.bottom })
      }}>
      <div className="flex justify-between gap-2">
        <span className="truncate">{step.name}</span>
        {step.state !== 'ok' && <span className="shrink-0 text-danger">{STATES[step.state] ?? step.state}</span>}
        {canEdit(step) &&
          <IconBtn label={`Edit ${step.name}`} disabled={editing} onClick={() => onEdit(step)}>
            <Pencil className="size-3.5" />
          </IconBtn>}
      </div>
      {!step.undone && (step.state === 'failed' || step.state === 'needs_attention' || step.kind === 'embedded_import' || step.constructionPlane) && step.message &&
        <p className="mt-1 break-words text-text-2">{step.message}</p>}
      {!step.undone && (step.missingInput || step.state === 'failed') && <div className="mt-1 flex flex-wrap gap-1.5">
        <Btn size="sm" disabled={repairing} aria-label={`${keepLabel(step)}: ${step.name}`} onClick={() => onKeep(step)}>{keepLabel(step)}</Btn>
        {step.missingInput && <Btn size="sm" disabled={repairing}
          aria-label={`Repair ${step.name}`} onClick={() => onRepair(step)}>Repair input</Btn>}
      </div>}
    </li>
  )
}, (a, b) => a.editing === b.editing && a.repairing === b.repairing && a.onEdit === b.onEdit && a.onRepair === b.onRepair && a.onKeep === b.onKeep && a.onMenu === b.onMenu &&
  JSON.stringify(a.step) === JSON.stringify(b.step))
