import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'jotai'
import App from './App'
import { ChatRunManager, ChatRunProvider } from './chatRunManager'
import './styles.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
})
const chatRunManager = new ChatRunManager(queryClient)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Provider>
      <QueryClientProvider client={queryClient}>
        <ChatRunProvider manager={chatRunManager}>
          <App />
        </ChatRunProvider>
      </QueryClientProvider>
    </Provider>
  </React.StrictMode>,
)
