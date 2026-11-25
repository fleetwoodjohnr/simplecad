import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { ChevronDown, ClipboardCheck, FileInput, PenLine, Ruler, Shapes, type LucideIcon } from 'lucide-react'
import { Tip, cx } from '../ui'
import { commands, type CommandActions, type CommandId } from './commands'

type Tool = { id: CommandId; icon: LucideIcon }

// 0.6.0's short ribbon, as 0.2.0's: what starts a model or looks at it. Every other tool is in the bar beside the
// selection, the right-click menu and command search (S), which lists them all.
const groups: { group: string; tools: Tool[] }[] = [
  { group: 'Create', tools: [{ id: 'shape', icon: Shapes }, { id: 'sketch', icon: PenLine }, { id: 'importModel', icon: FileInput }] },
  { group: 'Inspect', tools: [{ id: 'measure', icon: Ruler }, { id: 'printCheck', icon: ClipboardCheck }] }
]

/** Shorter captions for long names, so the ribbon fits; each button keeps its full name for tooltips and screen readers. */
const captions: Record<string, string> = {
  'Push/Pull': 'Pull', 'Matching Part': 'Match', 'Face Texture': 'Texture', 'Stack & Place': 'Stack', 'Offset Plane': 'Offset',
  'Section Replace': 'Section', 'Print check': 'Print'
}

type ToolBtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { icon: LucideIcon; label: string; caret?: boolean }

/** An icon over its caption; below the ribbon's width breakpoint only the icon shows, and the tooltip names it. */
export const ToolBtn = forwardRef<HTMLButtonElement, ToolBtnProps>(function ToolBtn({ icon: Icon, label, caret, className, ...p }, ref) {
  return (
    <button ref={ref} type="button" aria-label={label} {...p} className={cx(
      'inline-flex h-11 min-w-9 shrink-0 select-none flex-col items-center justify-center gap-1 rounded-md px-1 text-text-2 transition-colors duration-100 hover:bg-hover hover:text-text disabled:pointer-events-none disabled:opacity-40 aria-pressed:bg-accent-soft aria-pressed:text-accent-ink @max-[1360px]:h-8',
      className)}>
      <Icon className="size-[18px]" strokeWidth={1.75} aria-hidden />
      <span className="flex items-center gap-0.5 text-[10.5px] leading-none @max-[1360px]:hidden">{captions[label] ?? label}{caret && <ChevronDown className="size-2.5" aria-hidden />}</span>
    </button>
  )
})

/** The tooltip has to sit on a wrapper: a disabled button gets no pointer events, and its hint matters most then. */
function Tipped({ label, hint, shortcut, open, children }: { label: string; hint?: string; shortcut?: string; open?: boolean; children: ReactNode }) {
  return <Tip label={hint ? `${label} · ${hint}` : label} shortcut={shortcut} open={open}><span className="inline-flex">{children}</span></Tip>
}

export function Ribbon({ actions, active }: { actions: CommandActions; active: Partial<Record<CommandId, boolean>> }) {
  return <>
    {groups.map(({ group, tools }) => (
      <div key={group} role="group" aria-label={group} className="flex h-full shrink-0 flex-col items-center justify-center border-r border-border px-1">
        <div className="flex items-center gap-0.5">
          {tools.map((tool) => {
            const action = actions[tool.id]
            const command = commands[tool.id]
            return <Tipped key={tool.id} label={command.label} hint={action.enabled ? undefined : action.hint}
              shortcut={'shortcut' in command ? command.shortcut : undefined}>
              <ToolBtn icon={tool.icon} label={command.label} disabled={!action.enabled} onClick={action.run}
                aria-pressed={active[tool.id] ?? undefined} />
            </Tipped>
          })}
        </div>
        <span className="pt-0.5 text-[9.5px] font-medium uppercase tracking-wide text-text-3 @max-[1360px]:hidden">{group}</span>
      </div>
    ))}
  </>
}
