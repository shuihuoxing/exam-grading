import { createRouter, createWebHistory } from 'vue-router'
import { useGradingStore } from './stores/grading'

const routes = [
  { path: '/', name: 'upload', component: () => import('./views/UploadView.vue') },
  {
    path: '/review',
    name: 'review',
    component: () => import('./views/ReviewView.vue'),
    beforeEnter: () => {
      const store = useGradingStore()
      if (!store.result) return '/'
    },
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
