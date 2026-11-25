import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/ibm-plex-sans/700.css'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/600.css'
import './theme/tokens.css'
import './theme/echarts'      // 显式注册 ea-obs 主题 —— 修复旧 bug D

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import { router } from './router'
import { initToken } from './api/http'

initToken()

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
