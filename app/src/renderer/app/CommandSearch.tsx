import { useState } from 'react'
import { Btn, inputCls, Modal } from '../ui'
import { barLabel, type BarActions, type BarTool } from './ContextBar'
import { commands, formatShortcut, type CommandId } from './commands'

/** Everything search offers: every command, and what the selection bar does straight to parts. 0.6.0's short ribbon
 * leaves most tools to the bar, the right-click menu and here, so every one must be here. */
export const searchable: BarTool[] = [...Object.keys(commands) as CommandId[], 'subtract', 'join', 'intersect', 'group', 'ungroup', 'hide']

export function CommandSearch({ actions, close }: { actions: BarActions; close: () => void }) {
  const [query, setQuery] = useState('')
  const results = searchable.filter((id) => barLabel(id).toLowerCase().includes(query.trim().toLowerCase()))
  // At once, so the tool is open before anything else happens; a field it focuses keeps the focus (see Modal).
  const run = (id: BarTool) => {
    close()
    actions[id].run()
  }
  return (
    <Modal title="Command search" open onOpenChange={(open) => { if (!open) close() }}>
      <input autoFocus aria-label="Search commands" value={query} className={`${inputCls} w-full`}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== 'Enter') return
          const first = results.find((id) => actions[id].enabled)
          if (first) run(first)
        }} />
      <ul className="mt-2 grid gap-0.5">
        {results.map((id) => {
          const command: { label: string; shortcut?: string } | undefined = id in commands ? commands[id as CommandId] : undefined
          return (
            <li key={id}>
              <Btn className="w-full justify-between" variant="ghost" aria-label={barLabel(id)} disabled={!actions[id].enabled} onClick={() => run(id)}>
                {barLabel(id)}
                {command?.shortcut && <span className="text-xs text-text-3">{formatShortcut(command.shortcut)}</span>}
              </Btn>
            </li>
          )
        })}
      </ul>
      {!results.length && <p className="pt-2 text-xs text-text-2">No matching commands</p>}
    </Modal>
  )
}
