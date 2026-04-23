import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Apply stored theme synchronously, before React mounts, to avoid a
// dark-to-light flash on light-mode users.
try {
  if (localStorage.getItem("vvi.theme") === "light") {
    document.documentElement.classList.add("theme-light");
  }
} catch {}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
