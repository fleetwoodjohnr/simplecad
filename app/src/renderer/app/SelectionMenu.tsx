import { Fragment } from 'react'
import { Menu, MenuContent, MenuItem, MenuSep, MenuTrigger } from '../ui'

export interface MenuEntry {
  label: string
  run: () => void
  /** Drawn after a separator: the next group of actions. */
  group?: boolean
}

/** SimpleCAD 0.4.4's right-click menu: what can be done to what was clicked, at the pointer. */
export function SelectionMenu({ at, label, items, onClose }: {
  at: { x: number; y: number }
  label: string
  items: MenuEntry[]
  onClose: () => void
}) {
  return (
    <Menu open onOpenChange={(open) => { if (!open) onClose() }}>
      <MenuTrigger asChild><span aria-hidden className="pointer-events-none fixed size-0" style={{ left: at.x, top: at.y }} /></MenuTrigger>
      <MenuContent aria-label={label} side="bottom" sideOffset={2}>
        {items.map((item, index) => <Fragment key={`${index}:${item.label}`}>
          {item.group && index > 0 && <MenuSep />}
          <MenuItem onSelect={item.run}>{item.label}</MenuItem>
        </Fragment>)}
      </MenuContent>
    </Menu>
  )
}
