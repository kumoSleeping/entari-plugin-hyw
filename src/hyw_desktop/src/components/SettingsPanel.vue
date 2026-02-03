<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import { invoke } from '@tauri-apps/api/core'

const emit = defineEmits<{
  (e: 'close'): void
}>()

const serverStatus = ref<'checking' | 'online' | 'offline'>('checking')
const serverConfig = ref<any>(null)

const checkServer = async () => {
  serverStatus.value = 'checking'
  try {
    const online = await invoke<boolean>('check_server_status')
    serverStatus.value = online ? 'online' : 'offline'

    if (online) {
      serverConfig.value = await invoke('get_server_config')
    }
  } catch {
    serverStatus.value = 'offline'
  }
}

onMounted(() => {
  checkServer()
})
</script>

<template>
  <div class="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50" @click.self="emit('close')">
    <div class="bg-white rounded-xl shadow-2xl w-[400px] max-h-[500px] overflow-hidden">
      <!-- Header -->
      <div class="flex items-center justify-between px-5 py-4 border-b border-gray-100">
        <h2 class="text-lg font-bold text-gray-800">Settings</h2>
        <button @click="emit('close')" class="p-1 hover:bg-gray-100 rounded-lg transition-colors">
          <Icon icon="mdi:close" class="w-5 h-5 text-gray-500" />
        </button>
      </div>

      <!-- Content -->
      <div class="p-5 space-y-6">
        <!-- Server Status -->
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-sm font-medium text-gray-700">HYW Server</span>
            <div class="flex items-center gap-2">
              <span
                class="w-2 h-2 rounded-full"
                :class="{
                  'bg-green-500': serverStatus === 'online',
                  'bg-red-500': serverStatus === 'offline',
                  'bg-yellow-500 animate-pulse': serverStatus === 'checking'
                }"
              ></span>
              <span class="text-xs text-gray-500 capitalize">{{ serverStatus }}</span>
              <button
                @click="checkServer"
                class="p-1 hover:bg-gray-100 rounded transition-colors"
                :disabled="serverStatus === 'checking'"
              >
                <Icon icon="mdi:refresh" class="w-4 h-4 text-gray-400" :class="{ 'animate-spin': serverStatus === 'checking' }" />
              </button>
            </div>
          </div>

          <div v-if="serverConfig" class="text-xs font-mono text-gray-400 bg-gray-50 rounded p-2">
            <div>Host: {{ serverConfig.server?.host }}:{{ serverConfig.server?.port }}</div>
          </div>
        </div>

        <!-- Keyboard Shortcut -->
        <div class="space-y-2">
          <span class="text-sm font-medium text-gray-700">Global Shortcut</span>
          <div class="flex items-center gap-2 text-xs">
            <kbd class="px-2 py-1 bg-gray-100 rounded border border-gray-200 font-mono">⌘</kbd>
            <span class="text-gray-400">+</span>
            <kbd class="px-2 py-1 bg-gray-100 rounded border border-gray-200 font-mono">G</kbd>
            <span class="text-gray-500 ml-2">Toggle window</span>
          </div>
        </div>

        <!-- Version Info -->
        <div class="pt-4 border-t border-gray-100">
          <div class="text-xs text-gray-400 text-center">
            HYW Desktop v0.1.0
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
