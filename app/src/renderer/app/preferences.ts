import { useEffect, useState } from 'react'
import { defaults, sanitisePreferences, type Preferences, type Theme } from '@/shared/preferences'

const key = 'simplecad.preferences.v1'

function currentPreferences() {
  try {
    const stored = localStorage.getItem(key)
    if (stored) return sanitisePreferences(JSON.parse(stored))
  } catch {
    // Leave malformed browser storage behind and fall back to migration.
  }
}

function previewTheme(): Theme | undefined {
  const stored = localStorage.getItem('simplecad.theme')
  return stored === 'light' || stored === 'dark' ? stored : undefined
}

export function usePreferences() {
  const [preferences, setPreferences] = useState(() => currentPreferences() ?? { ...defaults, theme: previewTheme() ?? defaults.theme })
  const [loaded, setLoaded] = useState(() => currentPreferences() !== undefined)
  const apply = (next: Preferences) => {
    const clean = sanitisePreferences(next)
    localStorage.setItem(key, JSON.stringify(clean))
    setPreferences(clean)
    setLoaded(true)
  }
  useEffect(() => {
    if (loaded) return
    let active = true
    void window.simplecad.legacyPreferences().then((found) => {
      if (!active || currentPreferences()) return
      apply({ ...found ?? defaults, theme: previewTheme() ?? found?.theme ?? defaults.theme })
    }, () => { if (active) setLoaded(true) })
    return () => { active = false }
  }, [loaded])
  return { preferences, apply, loaded }
}
