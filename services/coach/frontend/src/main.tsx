import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { CoachChat } from './CoachChat'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CoachChat />
  </StrictMode>,
)
