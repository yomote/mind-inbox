import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { initAuth } from './auth/msal'

// 認証が有効な環境では、ログインリダイレクトからの戻りを回収してから描画する (#69)。
// 無効 (ローカル) の場合 initAuth は即座に解決するので、起動フローは変わらない。
initAuth().finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
