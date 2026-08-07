import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'jotai'
import App from './App'
import { AgentRunManager, AgentRunProvider } from './agentRunManager'
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
const agentRunManager = new AgentRunManager(queryClient)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Provider>
      <QueryClientProvider client={queryClient}>
        <AgentRunProvider manager={agentRunManager}>
          <ChatRunProvider manager={chatRunManager}>
            <App />
          </ChatRunProvider>
        </AgentRunProvider>
      </QueryClientProvider>
    </Provider>
  </React.StrictMode>,
)
