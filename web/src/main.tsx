import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { initializePlatform } from './platform/index.ts'
import { PlatformProvider } from './platform/platform-context.tsx'
import { applyThemePreference } from './lib/theme.ts'

applyThemePreference()

const platform = await initializePlatform()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PlatformProvider adapter={platform}>
      <App />
    </PlatformProvider>
  </StrictMode>,
)
