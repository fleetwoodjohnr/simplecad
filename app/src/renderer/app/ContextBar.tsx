import { Fragment, type RefObject } from 'react'
import {
  ArrowUpFromLine, CircleDot, Ellipsis, Move, PenLine, Rotate3d, Spline, SquareDashed, type LucideIcon
} from 'lucide-react'
import { Btn, IconBtn, Menu, MenuContent, MenuItem, MenuSep, MenuTrigger, inputCls } from '../ui'
import { commands, type CommandActions, type CommandId } from './commands'

interface Props {
  actions: CommandActions
  planar: boolean
  distance: string
  canPull: boolean
  distanceRef: RefObject<HTMLInputElement | null>
  onDistance: (value: string) => void
  onPull: () => void
}

const planarTools: [CommandId, LucideIcon][] = [
  ['move', Move], ['rotate', Rotate3d], ['sketch', PenLine], ['hole', CircleDot], ['offsetPlane', SquareDashed]
]
const planarMore: CommandId[] = ['shell', 'text', 'faceTexture', 'stack', 'measure', 'clear']
const curvedTools: [CommandId, LucideIcon][] = [['thread', Spline], ['move', Move], ['rotate', Rotate3d]]

/** The tools for what is selected, beside it in the view: the pull distance first, then the rest. */
export function ContextBar({ actions, planar, distance, canPull, distanceRef, onDistance, onPull }: Props) {
  const more: CommandId[] = planar ? planarMore : ['shell', 'faceTexture', 'stack', 'measure', 'clear']
  const value = Number(distance)
  return (
    <div role="toolbar" aria-label="Selection tools"
      className="flex items-center gap-0.5 rounded-xl border border-border bg-elev/95 p-1 text-xs shadow-lg backdrop-blur">
      {planar && <form className="flex items-center gap-1 border-r border-border pr-1" onSubmit={(e) => { e.preventDefault(); onPull() }}>
        <ArrowUpFromLine className="ml-1 size-4 text-accent-ink" aria-hidden />
        <input ref={distanceRef} aria-label="Pull distance" type="number" step="any" placeholder="Distance" value={distance}
          className={`${inputCls} max-w-20`} onChange={(e) => onDistance(e.target.value)} />
        <span className="text-text-2">mm</span>
        <Btn type="submit" variant="primary" size="sm" disabled={!canPull || distance === '' || !Number.isFinite(value) || value === 0}>Pull</Btn>
      </form>}
      {(planar ? planarTools : curvedTools).map(([id, Icon]) =>
        <IconBtn key={id} label={commands[id].label} disabled={!actions[id].enabled} onClick={actions[id].run}><Icon className="size-4" /></IconBtn>)}
      <Menu>
        <MenuTrigger asChild><IconBtn label="More tools" tip={false}><Ellipsis className="size-4" /></IconBtn></MenuTrigger>
        <MenuContent side="top" align="end">
          {more.map((id) => <Fragment key={id}>
            {id === 'clear' && <MenuSep />}
            <MenuItem disabled={!actions[id].enabled} onSelect={actions[id].run}>{commands[id].label}</MenuItem>
          </Fragment>)}
        </MenuContent>
      </Menu>
    </div>
  )
}
