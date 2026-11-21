import { createRoot } from 'react-dom/client'
import './styles.css'
import { App } from './app/App'
import { crashEngine } from './state/engine'

// For the engine isolation drill in tests/e2e; the engine refuses it unless started for the drill.
;(window as unknown as { simplecadDrill: unknown }).simplecadDrill = { crashEngine }

createRoot(document.getElementById('root')!).render(<App />)
