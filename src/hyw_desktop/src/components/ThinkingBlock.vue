<script setup lang="ts">
import { computed } from 'vue'
import { AgentEvent } from '../stores/agent'

const props = defineProps<{
  event: AgentEvent
  index: number
}>()

const emit = defineEmits(['toggle'])

const icon = computed(() => {
  switch (props.event.type) {
    case 'thought': return '💭'
    case 'tool_call': return '🛠️'
    case 'tool_result': return '✅'
    case 'agent_start': return '🚀'
    case 'agent_finish': return '🏁'
    default: return '❓'
  }
})

const title = computed(() => {
  switch (props.event.type) {
    case 'thought': return 'Thinking...'
    case 'tool_call': return 'Using Tools'
    case 'tool_result': 
        return `Result: ${props.event.data.name}`
    case 'agent_start': return 'Task Started'
    case 'agent_finish': return 'Task Finished'
    default: return props.event.type
  }
})
</script>

<template>
  <div class="border-l-2 border-base-300 ml-2 pl-4 py-2 relative group">
    <!-- Timeline Dot -->
    <div class="absolute -left-[9px] top-3 w-4 h-4 rounded-full bg-base-200 border-2 border-primary group-hover:bg-primary transition-colors"></div>
    
    <!-- Header -->
    <div 
      class="flex items-center gap-2 cursor-pointer select-none hover:bg-base-200/50 p-1 rounded transition-colors"
      @click="emit('toggle', index)"
    >
      <span class="text-lg">{{ icon }}</span>
      <span class="font-medium text-sm opacity-80">{{ title }}</span>
      <span class="text-xs opacity-50 ml-auto">{{ new Date(event.timestamp * 1000).toLocaleTimeString() }}</span>
      <span class="transform transition-transform text-xs opacity-50" :class="{ '-rotate-90': event.collapsed }">▼</span>
    </div>
    
    <!-- Content -->
    <div v-if="!event.collapsed" class="mt-2 text-sm bg-base-200/30 p-2 rounded overflow-x-auto animate-fade-in">
        
      <!-- Thought -->
      <div v-if="event.type === 'thought'" class="opacity-70 italic">
        Planning next steps (Round {{ event.data.round }})...
      </div>
      
      <!-- Tool Call -->
      <div v-if="event.type === 'tool_call'" class="flex flex-col gap-2">
        <div v-for="tool in event.data.tools" :key="tool.id" class="bg-base-300 p-2 rounded">
          <div class="font-bold text-primary flex items-center gap-2">
              <span>{{ tool.name }}</span>
          </div>
          <pre class="text-xs mt-1 overflow-x-auto bg-base-100 p-1 rounded opacity-80">{{ JSON.stringify(tool.args, null, 2) }}</pre>
        </div>
      </div>
      
      <!-- Tool Result -->
      <div v-if="event.type === 'tool_result'">
          <div v-if="event.data.name === 'web_tool'">
               <div class="font-medium mb-2">{{ event.data.result.summary }}</div>
               <div v-for="(item, i) in event.data.result.results" :key="i" class="mt-2 pl-2 border-l-2 border-primary/20">
                   <a :href="item.url" target="_blank" class="text-primary hover:underline block truncate text-xs">{{ item.title || item.url }}</a>
                   <div class="text-xs opacity-60 line-clamp-2 mt-1">{{ item.snippet || item.content }}</div>
               </div>
          </div>
          <div v-else>
               <pre class="text-xs overflow-x-auto">{{ JSON.stringify(event.data.result, null, 2) }}</pre>
          </div>
      </div>
      
      <!-- Start/Finish -->
      <div v-if="event.type === 'agent_start'" class="text-xs opacity-60">
        Query: <span class="text-base-content">{{ event.data.query }}</span>
      </div>
      
       <!-- Finish (Stats) -->
      <div v-if="event.type === 'agent_finish'" class="text-xs opacity-60">
        Time: {{ event.data.stats?.total_time?.toFixed(2) }}s
      </div>

    </div>
  </div>
</template>

<style scoped>
.animate-fade-in {
  animation: fadeIn 0.2s ease-in-out;
}
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(-5px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
