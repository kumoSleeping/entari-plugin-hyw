import { defineStore } from 'pinia'
import { ref } from 'vue'

export type AgentEvent = {
  type: 'agent_start' | 'thought' | 'tool_call' | 'tool_result' | 'agent_finish'
  data: any
  timestamp: number
  collapsed?: boolean
}

export const useAgentStore = defineStore('agent', () => {
  const events = ref<AgentEvent[]>([])
  const status = ref<'idle' | 'thinking' | 'searching' | 'finished'>('idle')
  const ws = ref<WebSocket | null>(null)
  const isConnected = ref(false)
  const queryInput = ref('')
  const currentResponse = ref('') // To accumulate streamed response if we were streaming, but here we get final block. 
  // However, we can use this to show the final markdown.

  const connect = (url: string) => {
    if (ws.value) ws.value.close()
    
    ws.value = new WebSocket(url)
    
    ws.value.onopen = () => {
      isConnected.value = true
      console.log('WS Connected')
    }
    
    ws.value.onclose = () => {
      isConnected.value = false
      console.log('WS Disconnected')
      setTimeout(() => connect(url), 3000)
    }
    
    ws.value.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data)
        handleEvent(data)
      } catch (e) {
        console.error('Failed to parse WS message', e)
      }
    }
  }
  
  const handleEvent = (event: AgentEvent) => {
    // Auto-collapse previous thinking/tool blocks when new major phase starts
    if (['tool_call', 'agent_finish'].includes(event.type)) {
       events.value.forEach(e => {
         if (['thought', 'tool_call', 'tool_result'].includes(e.type)) {
           e.collapsed = true
         }
       })
    }
    
    // Add new event
    events.value.push({ ...event, collapsed: false })
    
    // Update status and data
    if (event.type === 'agent_start') {
        status.value = 'thinking'
        currentResponse.value = ''
    }
    if (event.type === 'thought') status.value = 'thinking'
    if (event.type === 'tool_call') status.value = 'searching'
    if (event.type === 'agent_finish') {
        status.value = 'finished'
        if (event.data.response) {
            currentResponse.value = event.data.response
        }
    }
  }
  
  const sendQuery = (query: string, model?: string) => {
    if (!ws.value || !isConnected.value) return
    
    events.value = []
    status.value = 'thinking'
    currentResponse.value = ''
    
    ws.value.send(JSON.stringify({
      query,
      model,
      conversation_history: []
    }))
  }
  
  const toggleCollapse = (index: number) => {
      if (events.value[index]) {
          events.value[index].collapsed = !events.value[index].collapsed
      }
  }
  
  return {
    events,
    status,
    isConnected,
    connect,
    sendQuery,
    queryInput,
    currentResponse,
    toggleCollapse
  }
})
