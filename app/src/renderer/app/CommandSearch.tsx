import { useState } from 'react'
import { Btn, inputCls, Modal } from '../ui'
import { commands, formatShortcut, type CommandActions, type CommandId } from './commands'

export function CommandSearch({ actions, close }: { actions: CommandActions; close: () => void }) {
  const [query, setQuery] = useState('')
  const results = (Object.keys(commands) as CommandId[]).filter((id) =>
    commands[id].label.toLowerCase().includes(query.trim().toLowerCase())
  )
  const run = (id: CommandId) => {
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
          const command = commands[id]
          return (
            <li key={id}>
              <Btn className="w-full justify-between" variant="ghost" disabled={!actions[id].enabled} onClick={() => run(id)}>
                {command.label}
                {'shortcut' in command && <span className="text-xs text-text-3">{formatShortcut(command.shortcut)}</span>}
              </Btn>
            </li>
          )
        })}
      </ul>
      {!results.length && <p className="pt-2 text-xs text-text-2">No matching commands</p>}
    </Modal>
  )
}
