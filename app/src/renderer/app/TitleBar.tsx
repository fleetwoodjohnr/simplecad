// The suite's title bar (Simple Viz's TitleBar), trimmed to what Phase 1 has: the mark, the name, the theme toggle
// and drawn window controls where the system does not draw its own. Save, undo/redo and command search return with
// the features behind them.

import { useEffect, useState } from 'react'
import { Minus, Moon, Square, Sun, X } from 'lucide-react'
import { IconBtn } from '../ui'
import logo from '../../../resources/icon.svg'

function useOverlayInset(): number {
  const [inset, setInset] = useState(0)
  useEffect(() => {
    const wco = (navigator as Navigator & { windowControlsOverlay?: { visible: boolean; getTitlebarAreaRect(): DOMRect; addEventListener(t: string, f: () => void): void; removeEventListener(t: string, f: () => void): void } }).windowControlsOverlay
    if (!wco) return
    const update = () => setInset(wco.visible ? Math.max(0, window.innerWidth - wco.getTitlebarAreaRect().right) : 0)
    update()
    wco.addEventListener('geometrychange', update)
    return () => wco.removeEventListener('geometrychange', update)
  }, [])
  return inset
}

export function TitleBar({ dark, onToggleTheme, project, dirty }: { dark: boolean; onToggleTheme: () => void; project: string; dirty: boolean }) {
  const isMac = window.simplecad.platform === 'darwin'
  const inset = useOverlayInset()
  const customControls = !isMac && inset === 0

  return (
    <header className="drag flex h-11 shrink-0 items-center gap-1 border-b border-border bg-bg pr-2" style={{ paddingLeft: isMac ? 80 : 10, paddingRight: inset ? inset + 8 : undefined }}>
      <img src={logo} alt="" width={22} height={22} className="mr-1" />
      <span className="text-[13px] font-semibold">SimpleCAD</span>
      <span className="ml-2 rounded-md border border-border bg-panel px-2 py-1 text-xs text-text-2">{dirty ? `${project} •` : project}</span>
      <div className="h-full flex-1" />
      <div className="no-drag ml-1 flex items-center gap-0.5">
        <IconBtn label={dark ? 'Light mode' : 'Dark mode'} onClick={onToggleTheme} data-testid="theme-toggle">
          {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </IconBtn>
      </div>
      {customControls && (
        <div className="no-drag -mr-2 ml-2 flex h-full items-stretch">
          <button aria-label="Minimize" onClick={() => window.simplecad.windowMinimize()} className="flex w-11 items-center justify-center text-text-2 hover:bg-hover">
            <Minus className="size-4" />
          </button>
          <button aria-label="Maximize" onClick={() => window.simplecad.windowToggleMaximize()} className="flex w-11 items-center justify-center text-text-2 hover:bg-hover">
            <Square className="size-3.5" />
          </button>
          <button aria-label="Close window" onClick={() => window.simplecad.windowClose()} className="flex w-11 items-center justify-center text-text-2 hover:bg-[#e81123] hover:text-white">
            <X className="size-4" />
          </button>
        </div>
      )}
    </header>
  )
}
