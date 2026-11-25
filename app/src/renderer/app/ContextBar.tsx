import { Fragment, type RefObject } from 'react'
import {
  ArrowUpFromLine, Bolt, Box, Boxes, CircleDot, Combine, Ellipsis, EyeOff, Grid3x3, Layers, LayoutGrid, Magnet, Move, PenLine,
  Rotate3d, Ruler, Scaling, Scissors, SquareDashed, SquareMinus, SquareRoundCorner, SquareSlash, Spline, Trash2, Type,
  Ungroup, Paperclip, type LucideIcon
} from 'lucide-react'
import { Btn, IconBtn, Menu, MenuContent, MenuItem, MenuSep, MenuTrigger, Segmented, inputCls } from '../ui'
import { commands, type CommandActions, type CommandId } from './commands'

/** What the bar can offer: the commands, and what is done straight to the chosen parts. */
export type BarTool = CommandId | 'subtract' | 'join' | 'intersect' | 'group' | 'ungroup' | 'hide' | 'threadTogether'
export type BarActions = CommandActions & Record<Exclude<BarTool, CommandId>, CommandActions[CommandId]>
export type SelectionKind = 'face' | 'curvedFace' | 'edge' | 'part' | 'parts' | 'manyParts' | 'group' | 'threadPair' | 'matchable'

const labels: Record<Exclude<BarTool, CommandId>, string> = {
  subtract: 'Subtract', join: 'Join', intersect: 'Intersect', group: 'Group', ungroup: 'Ungroup', hide: 'Hide',
  threadTogether: 'Thread together'
}
export const barLabel = (id: BarTool) => id in labels ? labels[id as keyof typeof labels] : commands[id as CommandId].label
const icons: Partial<Record<BarTool, LucideIcon>> = {
  hole: CircleDot, sketch: PenLine, text: Type, shell: Box, thread: Spline, fillet: SquareRoundCorner, chamfer: SquareSlash,
  measure: Ruler, move: Move, rotate: Rotate3d, scale: Scaling, split: Scissors, hide: EyeOff, delete: Trash2,
  subtract: SquareMinus, join: Combine, intersect: Layers, group: Boxes, ungroup: Ungroup, stack: Magnet, clipJoint: Paperclip,
  arrange: LayoutGrid, combine: Combine, faceTexture: Grid3x3, offsetPlane: SquareDashed, threadTogether: Spline, matchingPart: Bolt
}

// Each selection's tools, as 0.2.0 ordered them: the likeliest first on the bar, the rest behind ⋯.
const lists: Record<SelectionKind, [BarTool[], BarTool[]]> = {
  face: [['hole', 'sketch', 'text', 'shell'], ['vent', 'faceTexture', 'offsetPlane', 'stack', 'measure']],
  curvedFace: [['thread', 'shell'], ['faceTexture', 'stack', 'measure']],
  // The edge form leads (Round or Bevel, a size, its handle on the edge); the full Fillet and Chamfer panels are behind ⋯.
  edge: [['measure'], ['fillet', 'chamfer']],
  part: [['move', 'rotate', 'scale', 'split', 'measure', 'hide', 'delete'], ['mirror', 'rectangularPattern', 'circularPattern', 'draft']],
  parts: [['subtract', 'join', 'intersect', 'group', 'stack', 'clipJoint'], ['arrange', 'hinge', 'threadedConnection', 'sectionReplace', 'hide', 'delete']],
  manyParts: [['arrange', 'subtract', 'join', 'group', 'stack'], ['intersect', 'hide', 'delete']],
  group: [['ungroup', 'move', 'rotate', 'hide', 'delete'], ['arrange']],
  // A shaft and a hole clicked on two parts (0.2.0's threaded pair), and a face once the project has a thread.
  threadPair: [['threadTogether'], []],
  matchable: [['matchingPart'], []]
}

/** Every tool offered for these selections, in order: the bar's, then its ⋯ (the right-click menu lists the same). */
export const selectionTools = (kinds: SelectionKind[]): BarTool[] =>
  [...new Set([...kinds.flatMap((kind) => lists[kind][0]), ...kinds.flatMap((kind) => lists[kind][1])])]

interface Pull {
  distance: string
  canPull: boolean
  distanceRef: RefObject<HTMLInputElement | null>
  onDistance: (value: string) => void
  onPull: () => void
}

/** Clicked edges: round or bevel them by a typed size (or by the handle on the edge). */
interface EdgeFinishBar {
  kind: 'fillet' | 'chamfer'
  size: string
  count: number
  canApply: boolean
  onKind: (kind: 'fillet' | 'chamfer') => void
  onSize: (value: string) => void
  onApply: () => void
}

interface Props {
  actions: BarActions
  /** What is selected, in the bar's order: a clicked face, then its part. */
  kinds: SelectionKind[]
  /** A face's pull distance, which leads the bar. */
  pull?: Pull
  /** Clicked edges' finish, which leads the bar. */
  edge?: EdgeFinishBar
}

/** The tools for what is selected, beside it in the view: the pull distance first, then each selection's tools. */
export function ContextBar({ actions, kinds, pull, edge }: Props) {
  const shown = new Set<BarTool>()
  const sections = kinds.map((kind) => lists[kind][0].filter((id) => !shown.has(id) && shown.add(id)))
  const more = kinds.flatMap((kind) => lists[kind][1]).filter((id) => !shown.has(id) && shown.add(id))
  const value = Number(pull?.distance)
  return (
    <div role="toolbar" aria-label="Selection tools"
      className="flex items-center gap-0.5 rounded-xl border border-border bg-elev/95 p-1 text-xs shadow-lg backdrop-blur">
      {pull && <form className="flex items-center gap-1 border-r border-border pr-1" onSubmit={(e) => { e.preventDefault(); pull.onPull() }}>
        <ArrowUpFromLine className="ml-1 size-4 text-accent-ink" aria-hidden />
        <input ref={pull.distanceRef} aria-label="Pull distance" type="number" step="any" placeholder="Distance" value={pull.distance}
          className={`${inputCls} max-w-20`} onChange={(e) => pull.onDistance(e.target.value)} />
        <span className="text-text-2">mm</span>
        <Btn type="submit" variant="primary" size="sm" disabled={!pull.canPull || pull.distance === '' || !Number.isFinite(value) || value === 0}>Pull</Btn>
      </form>}
      {edge && <form aria-label="Edge finish" className="flex items-center gap-1 border-r border-border pr-1"
        onSubmit={(e) => { e.preventDefault(); edge.onApply() }}>
        <Segmented label="Edge finish kind" value={edge.kind} onChange={edge.onKind}
          options={[{ value: 'fillet', label: 'Round' }, { value: 'chamfer', label: 'Bevel' }]} />
        <input aria-label="Edge size" type="number" step="any" min="0" placeholder="Size" value={edge.size}
          className={`${inputCls} max-w-16`} onChange={(e) => edge.onSize(e.target.value)} />
        <span className="text-text-2">mm{edge.count > 1 ? ` · ${edge.count} edges` : ''}</span>
        <Btn type="submit" variant="primary" size="sm" disabled={!edge.canApply}>Apply</Btn>
      </form>}
      {sections.map((ids, k) => <Fragment key={kinds[k]}>
        {k > 0 && <span aria-hidden className="mx-0.5 h-5 w-px bg-border" />}
        {ids.map((id) => {
          const Icon = icons[id] ?? Ellipsis
          return <IconBtn key={id} label={barLabel(id)} disabled={!actions[id].enabled} onClick={actions[id].run}><Icon className="size-4" /></IconBtn>
        })}
      </Fragment>)}
      <Menu>
        <MenuTrigger asChild><IconBtn label="More tools" tip={false}><Ellipsis className="size-4" /></IconBtn></MenuTrigger>
        <MenuContent side="top" align="end">
          {more.map((id) => <MenuItem key={id} disabled={!actions[id].enabled} onSelect={actions[id].run}>{barLabel(id)}</MenuItem>)}
          {more.length > 0 && <MenuSep />}
          <MenuItem disabled={!actions.clear.enabled} onSelect={actions.clear.run}>{commands.clear.label}</MenuItem>
        </MenuContent>
      </Menu>
    </div>
  )
}
