import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import {
  ArrowUpFromLine, ChevronDown, CircleDot, Grid3x3, Layers, Magnet, Move, Nut, PenLine, Rotate3d, Ruler, Shapes,
  Spline, SquareDashed, Type, type LucideIcon
} from 'lucide-react'
import { Menu, MenuContent, MenuItem, MenuTrigger, Tip, cx } from '../ui'
import { commands, type CommandActions, type CommandId } from './commands'

type Tool = { id: CommandId; icon: LucideIcon } | { menu: string; icon: LucideIcon; items: CommandId[]; hint: string }

export const sketchSolids: CommandId[] = ['extrude', 'revolve', 'sweep', 'loft', 'path_pattern']

const groups: { group: string; tools: Tool[] }[] = [
  { group: 'Create', tools: [
    { id: 'shape', icon: Shapes },
    { id: 'sketch', icon: PenLine },
    { menu: 'From Sketch', icon: Layers, items: sketchSolids, hint: 'Draw a sketch first' },
    { id: 'text', icon: Type },
    { id: 'matchingPart', icon: Nut }
  ] },
  { group: 'Modify', tools: [
    { id: 'pushPull', icon: ArrowUpFromLine },
    { id: 'move', icon: Move },
    { id: 'rotate', icon: Rotate3d },
    { id: 'hole', icon: CircleDot },
    { id: 'thread', icon: Spline },
    { id: 'faceTexture', icon: Grid3x3 }
  ] },
  { group: 'Place', tools: [{ id: 'stack', icon: Magnet }] },
  { group: 'Construct', tools: [{ id: 'offsetPlane', icon: SquareDashed }] },
  { group: 'Inspect', tools: [{ id: 'measure', icon: Ruler }] }
]

type ToolBtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { icon: LucideIcon; label: string; caret?: boolean }

/** An icon over its caption; below the ribbon's width breakpoint only the icon shows, and the tooltip names it. */
export const ToolBtn = forwardRef<HTMLButtonElement, ToolBtnProps>(function ToolBtn({ icon: Icon, label, caret, className, ...p }, ref) {
  return (
    <button ref={ref} type="button" aria-label={label} {...p} className={cx(
      'inline-flex h-11 min-w-10 shrink-0 select-none flex-col items-center justify-center gap-1 rounded-md px-1.5 text-text-2 transition-colors duration-100 hover:bg-hover hover:text-text disabled:pointer-events-none disabled:opacity-40 aria-pressed:bg-accent-soft aria-pressed:text-accent-ink @max-[1180px]:h-8',
      className)}>
      <Icon className="size-[18px]" strokeWidth={1.75} aria-hidden />
      <span className="flex items-center gap-0.5 text-[10.5px] leading-none @max-[1180px]:hidden">{label}{caret && <ChevronDown className="size-2.5" aria-hidden />}</span>
    </button>
  )
})

/** The tooltip has to sit on a wrapper: a disabled button gets no pointer events, and its hint matters most then. */
function Tipped({ label, hint, shortcut, children }: { label: string; hint?: string; shortcut?: string; children: ReactNode }) {
  return <Tip label={hint ? `${label} · ${hint}` : label} shortcut={shortcut}><span className="inline-flex">{children}</span></Tip>
}

export function Ribbon({ actions, active }: { actions: CommandActions; active: Partial<Record<CommandId, boolean>> }) {
  return <>
    {groups.map(({ group, tools }) => (
      <div key={group} role="group" aria-label={group} className="flex h-full shrink-0 flex-col items-center justify-center border-r border-border px-1.5">
        <div className="flex items-center gap-0.5">
          {tools.map((tool) => {
            if ('menu' in tool) {
              const enabled = tool.items.some((id) => actions[id].enabled)
              return <Tipped key={tool.menu} label={tool.menu} hint={enabled ? undefined : tool.hint}>
                <Menu>
                  <MenuTrigger asChild><ToolBtn icon={tool.icon} label={tool.menu} caret disabled={!enabled} /></MenuTrigger>
                  <MenuContent>
                    {tool.items.map((id) => <MenuItem key={id} disabled={!actions[id].enabled} onSelect={actions[id].run}>{commands[id].label}</MenuItem>)}
                  </MenuContent>
                </Menu>
              </Tipped>
            }
            const action = actions[tool.id]
            const command = commands[tool.id]
            return <Tipped key={tool.id} label={command.label} hint={action.enabled ? undefined : action.hint}
              shortcut={'shortcut' in command ? command.shortcut : undefined}>
              <ToolBtn icon={tool.icon} label={command.label} disabled={!action.enabled} onClick={action.run}
                aria-pressed={active[tool.id] ?? undefined} />
            </Tipped>
          })}
        </div>
        <span className="pt-0.5 text-[9.5px] font-medium uppercase tracking-wide text-text-3 @max-[1180px]:hidden">{group}</span>
      </div>
    ))}
  </>
}
