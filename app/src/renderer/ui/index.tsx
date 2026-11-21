import { forwardRef, useRef, useState, type ButtonHTMLAttributes, type ComponentProps, type ReactNode } from 'react'
import { DropdownMenu, Tooltip, Dialog, Popover, Switch as RSwitch, ContextMenu } from 'radix-ui'
import { Check, ChevronRight, X } from 'lucide-react'
import { formatShortcut } from '../app/commands'

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(' ')

// ---------- tooltip ----------

export const TooltipProvider = ({ children }: { children: ReactNode }) => (
  <Tooltip.Provider delayDuration={450} skipDelayDuration={200}>
    {children}
  </Tooltip.Provider>
)

export function Tip({ label, shortcut, children, side = 'bottom' }: { label?: ReactNode; shortcut?: string; children: ReactNode; side?: 'top' | 'bottom' | 'left' | 'right' }) {
  if (!label) return <>{children}</>
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content side={side} sideOffset={6} className="pop-in z-[100] flex items-center gap-2 rounded-md bg-[#1c1c22] px-2 py-1 text-[11.5px] font-medium text-white shadow-lg dark:bg-[#f0f0f3] dark:text-[#18181b]">
          {label}
          {shortcut && <span className="opacity-55">{formatShortcut(shortcut)}</span>}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
}

// ---------- buttons ----------

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'soft'; size?: 'sm' | 'md' | 'lg' }

export const Btn = forwardRef<HTMLButtonElement, BtnProps>(function Btn({ variant = 'secondary', size = 'md', className, ...p }, ref) {
  return (
    <button
      ref={ref}
      {...p}
      className={cx(
        'inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-[background,box-shadow,color,transform] duration-150 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-45',
        size === 'sm' && 'h-7 px-2.5 text-[12px]',
        size === 'md' && 'h-8 px-3 text-[13px]',
        size === 'lg' && 'h-10 px-4 text-[14px]',
        variant === 'primary' && 'bg-accent text-accent-fg shadow-[inset_0_1px_0_rgb(255_255_255/0.18),0_1px_2px_rgb(0_0_0/0.15)] hover:brightness-110',
        variant === 'secondary' && 'border border-border bg-panel text-text shadow-[0_1px_1px_rgb(0_0_0/0.04)] hover:bg-hover',
        variant === 'ghost' && 'text-text-2 hover:bg-hover hover:text-text',
        variant === 'soft' && 'bg-accent-soft text-accent-ink hover:brightness-105',
        variant === 'danger' && 'bg-danger-strong text-white hover:brightness-110',
        className
      )}
    />
  )
})

type IconBtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { label: string; shortcut?: string; active?: boolean; size?: 'sm' | 'md'; tip?: boolean }

export const IconBtn = forwardRef<HTMLButtonElement, IconBtnProps>(function IconBtn({ label, shortcut, active, size = 'md', tip = true, className, children, ...p }, ref) {
  const btn = (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      aria-pressed={active === undefined ? undefined : active}
      {...p}
      className={cx(
        'inline-flex shrink-0 select-none items-center justify-center rounded-md text-text-2 transition-colors duration-100 hover:bg-hover hover:text-text disabled:pointer-events-none disabled:opacity-40',
        size === 'md' ? 'h-7 min-w-7 px-1' : 'h-6 min-w-6 px-0.5',
        active && 'bg-accent-soft text-accent-ink hover:bg-accent-soft hover:text-accent-ink',
        className
      )}
    >
      {children}
    </button>
  )
  return tip ? (
    <Tip label={label} shortcut={shortcut}>
      {btn}
    </Tip>
  ) : (
    btn
  )
})

export const Kbd = ({ children }: { children: ReactNode }) => (
  <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-border bg-panel-2 px-1.5 font-sans text-[11px] font-medium text-text-2 shadow-[0_1px_0_var(--border)]">{children}</kbd>
)

// ---------- menus ----------

// Command menus don't trap focus. A modal menu pulls back the focus an item's action moves to the document until the close
// has rendered and Radix's own refocus (a timer) has run; keys typed meanwhile went to the menu (a space chose the item again).
export const Menu = (p: ComponentProps<typeof DropdownMenu.Root>) => <DropdownMenu.Root modal={false} {...p} />
export const MenuTrigger = DropdownMenu.Trigger

const surface = 'pop-in z-[90] min-w-[180px] overflow-hidden rounded-xl border border-border bg-elev p-1 text-[13px] text-text shadow-[var(--shadow-pop)] outline-none'

/** A menu taller than the room left in the window scrolls instead of running off screen. */
const menuFit = 'max-h-[var(--radix-popper-available-height)] overflow-y-auto'

export function MenuContent({ children, className, align = 'start', onCloseAutoFocus, onEscapeKeyDown, ...p }: ComponentProps<typeof DropdownMenu.Content>) {
  // After choosing an item, focus stays where the item put it (the document, a dialog) or returns to the
  // visible document — not to the trigger button — so the next keystroke goes into the text.
  // Escape still returns focus to the trigger.
  const escaped = useRef(false)
  const [opener] = useState(() => document.activeElement as HTMLElement | null)
  return (
    <DropdownMenu.Portal>
      <DropdownMenu.Content
        sideOffset={6}
        align={align}
        collisionPadding={8}
        onEscapeKeyDown={(e) => {
          escaped.current = true
          onEscapeKeyDown?.(e)
        }}
        onCloseAutoFocus={(e) => {
          onCloseAutoFocus?.(e)
          if (e.defaultPrevented || escaped.current) return
          e.preventDefault()
          // Now, not a frame later: by then another menu may be open, which moving focus would close.
          const a = document.activeElement
          if (a && a !== document.body && !a.closest('[role=menu]')) return
          const docEl = [...document.querySelectorAll<HTMLElement>('[data-doc-focus]')].find((el) => el.offsetParent !== null)
          ;(docEl ?? (opener?.isConnected ? opener : null))?.focus()
        }}
        {...p}
        className={cx(surface, menuFit, className)}
      >
        {children}
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  )
}

const itemCls =
  'relative flex h-8 cursor-default select-none items-center gap-2.5 rounded-lg px-2.5 outline-none data-[disabled]:pointer-events-none data-[highlighted]:bg-hover data-[disabled]:opacity-40 [&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:text-text-2'

export function MenuItem({ children, shortcut, icon, className, ...p }: ComponentProps<typeof DropdownMenu.Item> & { shortcut?: string; icon?: ReactNode }) {
  return (
    <DropdownMenu.Item {...p} className={cx(itemCls, className)}>
      {icon}
      <span className="flex-1 truncate">{children}</span>
      {shortcut && <span className="ml-4 text-[11.5px] text-text-3">{formatShortcut(shortcut)}</span>}
    </DropdownMenu.Item>
  )
}

export function MenuCheck({ children, checked, ...p }: ComponentProps<typeof DropdownMenu.CheckboxItem>) {
  return (
    <DropdownMenu.CheckboxItem checked={checked} {...p} className={cx(itemCls, 'pl-8')}>
      <DropdownMenu.ItemIndicator className="absolute left-2.5 inline-flex">
        <Check className="size-4 text-accent-ink" />
      </DropdownMenu.ItemIndicator>
      {children}
    </DropdownMenu.CheckboxItem>
  )
}

export const MenuSep = () => <DropdownMenu.Separator className="-mx-1 my-1 h-px bg-border" />
export const MenuLabel = ({ children }: { children: ReactNode }) => <DropdownMenu.Label className="px-2.5 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-text-3">{children}</DropdownMenu.Label>

export function SubMenu({ label, icon, children }: { label: ReactNode; icon?: ReactNode; children: ReactNode }) {
  return (
    <DropdownMenu.Sub>
      <DropdownMenu.SubTrigger className={cx(itemCls, 'data-[state=open]:bg-hover')}>
        {icon}
        <span className="flex-1">{label}</span>
        <ChevronRight className="size-3.5 text-text-3" />
      </DropdownMenu.SubTrigger>
      <DropdownMenu.Portal>
        <DropdownMenu.SubContent sideOffset={4} collisionPadding={8} className={cx(surface, menuFit)}>
          {children}
        </DropdownMenu.SubContent>
      </DropdownMenu.Portal>
    </DropdownMenu.Sub>
  )
}

// ---------- context menu ----------

export const CtxMenu = (p: ComponentProps<typeof ContextMenu.Root>) => <ContextMenu.Root modal={false} {...p} />
export const CtxTrigger = ContextMenu.Trigger
export function CtxContent({ children }: { children: ReactNode }) {
  return (
    <ContextMenu.Portal>
      <ContextMenu.Content collisionPadding={8} className={surface}>
        {children}
      </ContextMenu.Content>
    </ContextMenu.Portal>
  )
}
export function CtxItem({ children, shortcut, icon, ...p }: ComponentProps<typeof ContextMenu.Item> & { shortcut?: string; icon?: ReactNode }) {
  return (
    <ContextMenu.Item {...p} className={itemCls}>
      {icon}
      <span className="flex-1">{children}</span>
      {shortcut && <span className="ml-4 text-[11.5px] text-text-3">{formatShortcut(shortcut)}</span>}
    </ContextMenu.Item>
  )
}
export const CtxSep = () => <ContextMenu.Separator className="-mx-1 my-1 h-px bg-border" />
export function CtxSub({ label, icon, children }: { label: ReactNode; icon?: ReactNode; children: ReactNode }) {
  return (
    <ContextMenu.Sub>
      <ContextMenu.SubTrigger className={cx(itemCls, 'data-[state=open]:bg-hover')}>
        {icon}
        <span className="flex-1">{label}</span>
        <ChevronRight className="size-3.5 text-text-3" />
      </ContextMenu.SubTrigger>
      <ContextMenu.Portal>
        <ContextMenu.SubContent sideOffset={4} className={surface}>
          {children}
        </ContextMenu.SubContent>
      </ContextMenu.Portal>
    </ContextMenu.Sub>
  )
}

// ---------- popover ----------

export const Pop = Popover.Root
export const PopTrigger = Popover.Trigger
export const PopClose = Popover.Close
export function PopContent({ children, className, ...p }: ComponentProps<typeof Popover.Content>) {
  return (
    <Popover.Portal>
      <Popover.Content sideOffset={6} collisionPadding={8} {...p} className={cx(surface, 'p-3', className)}>
        {children}
      </Popover.Content>
    </Popover.Portal>
  )
}

// ---------- dialog ----------

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  width = 480
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  title: ReactNode
  description?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  width?: number
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fade-in fixed inset-0 z-[80] bg-black/25 backdrop-blur-[2px] dark:bg-black/50" />
        <Dialog.Content
          style={{ width }}
          // Dialogs that close by giving the grid focus (closeDialog, focusGrid) keep it there: Radix's own return of focus
          // to the opener runs on a timer, later, and would take it away again.
          onCloseAutoFocus={(e) => document.activeElement?.hasAttribute('data-doc-focus') && e.preventDefault()}
          className="dialog-in fixed left-1/2 top-1/2 z-[81] max-h-[86vh] max-w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-2xl border border-border bg-elev text-text shadow-[var(--shadow-pop)] outline-none"
        >
          <div className="flex items-start justify-between gap-4 px-6 pb-2 pt-5">
            <div>
              <Dialog.Title className="text-[16px] font-semibold tracking-[-0.01em]">{title}</Dialog.Title>
              {description ? <Dialog.Description className="mt-1 text-[13px] leading-relaxed text-text-2">{description}</Dialog.Description> : <Dialog.Description className="sr-only">{title}</Dialog.Description>}
            </div>
            <Dialog.Close asChild>
              <IconBtn label="Close" tip={false} className="-mr-2 -mt-1">
                <X className="size-4" />
              </IconBtn>
            </Dialog.Close>
          </div>
          {children && <div className="max-h-[calc(86vh-140px)] overflow-y-auto px-6 py-3">{children}</div>}
          {footer && <div className="flex items-center justify-end gap-2 border-t border-border bg-panel-2 px-6 py-3">{footer}</div>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

// ---------- form controls ----------

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <RSwitch.Root
      checked={checked}
      onCheckedChange={onChange}
      aria-label={label}
      className="relative h-[18px] w-[30px] shrink-0 rounded-full bg-border-strong transition-colors data-[state=checked]:bg-accent"
    >
      <RSwitch.Thumb className="block size-[14px] translate-x-[2px] rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[14px]" />
    </RSwitch.Root>
  )
}

export const inputCls =
  'h-8 w-full rounded-lg border border-border bg-panel px-2.5 text-[13px] text-text outline-none transition-shadow placeholder:text-text-3 focus:border-accent focus:shadow-[0_0_0_3px_var(--accent-soft)]'

export function Field({ label, children, hint }: { label: ReactNode; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[12px] font-medium text-text-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11.5px] text-text-3">{hint}</span>}
    </label>
  )
}

export function NumberInput({ value, onChange, step = 1, min, max, unit, label, width = 76, placeholder = 'Auto' }: { value: number | null; onChange: (v: number | null) => void; step?: number; min?: number; max?: number; unit?: string; label: string; width?: number; placeholder?: string }) {
  return (
    <div className="relative" style={{ width }}>
      <input
        aria-label={label}
        type="number"
        step={step}
        min={min}
        max={max}
        value={value ?? ''}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        className={cx(inputCls, 'h-7 pr-6 text-[12px] tabular-nums [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none')}
      />
      {unit && <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-text-3">{unit}</span>}
    </div>
  )
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: ReactNode; title?: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg bg-hover p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={cx('h-7 rounded-md px-2.5 text-[12px] font-medium text-text-2 transition-all', value === o.value && 'bg-panel text-text shadow-[0_1px_2px_rgb(0_0_0/0.1)]')}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export const COLORS = [
  ['#000000', '#3f3f46', '#71717a', '#a1a1aa', '#d4d4d8', '#ffffff'],
  ['#b91c1c', '#c2410c', '#a16207', '#15803d', '#1d4ed8', '#7e22ce'],
  ['#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6', '#a855f7'],
  ['#fecaca', '#fed7aa', '#fef08a', '#bbf7d0', '#bfdbfe', '#e9d5ff']
]

export function ColorGrid({ onPick, onClear, clearLabel = 'Automatic', value }: { onPick: (c: string) => void; onClear?: () => void; clearLabel?: string; value?: string | null }) {
  return (
    <div className="w-[196px]">
      {onClear && (
        <button onClick={onClear} className="mb-2 flex h-7 w-full items-center gap-2 rounded-md px-2 text-[12px] text-text-2 hover:bg-hover">
          <span className="size-4 rounded border border-border bg-[linear-gradient(135deg,transparent_45%,#ef4444_45%,#ef4444_55%,transparent_55%)]" />
          {clearLabel}
        </button>
      )}
      <div className="grid grid-cols-6 gap-1.5">
        {COLORS.flat().map((c) => (
          <button
            key={c}
            aria-label={c}
            onClick={() => onPick(c)}
            className={cx('size-[26px] rounded-md border border-black/10 transition-transform hover:scale-110 dark:border-white/10', value?.toLowerCase() === c && 'ring-2 ring-accent ring-offset-1 ring-offset-elev')}
            style={{ background: c }}
          />
        ))}
      </div>
      <label className="mt-2 flex h-7 cursor-pointer items-center gap-2 rounded-md px-2 text-[12px] text-text-2 hover:bg-hover">
        <input type="color" className="size-4 cursor-pointer border-0 bg-transparent p-0" onChange={(e) => onPick(e.target.value)} />
        Custom color…
      </label>
    </div>
  )
}
