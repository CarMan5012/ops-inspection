import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import Dashboard from './views/Dashboard.vue'
import JobEditor from './views/JobEditor.vue'
import JobDetail from './views/JobDetail.vue'
import ItemEditor from './views/ItemEditor.vue'
import ItemBulk from './views/ItemBulk.vue'
import Runs from './views/Runs.vue'
import RunDetail from './views/RunDetail.vue'
import AlertDetail from './views/AlertDetail.vue'
import Periodic from './views/Periodic.vue'
import PeriodicEditor from './views/PeriodicEditor.vue'
import Profiles from './views/Profiles.vue'
import Notifications from './views/Notifications.vue'
import Settings from './views/Settings.vue'
import TemplateManager from './views/TemplateManager.vue'
import './style.css'
import './design-system.css'
import './table-system.css'
import ActionButton from './components/ActionButton.vue'
import IconButton from './components/IconButton.vue'
import ButtonGroup from './components/ButtonGroup.vue'
import TableText from './components/TableText.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/jobs' },
    { path: '/jobs', component: Dashboard },
    { path: '/jobs/new', component: JobEditor },
    { path: '/jobs/:id/edit', component: JobEditor },
    { path: '/jobs/:id', component: JobDetail },
    { path: '/jobs/:jobId/items/new', component: ItemEditor },
    { path: '/jobs/:id/items/add', component: ItemBulk },
    { path: '/jobs/:jobId/items/:id/edit', component: ItemEditor },
    { path: '/runs', component: Runs },
    { path: '/runs/:id', component: RunDetail },
    { path: '/runs/:id/report', component: () => import('./views/ReportPreview.vue') },
    { path: '/runs/:id/alerts/:resultId/:alertIndex', component: AlertDetail },
    { path: '/periodic', component: Periodic },
    { path: '/templates', component: TemplateManager },
    { path: '/report-templates', redirect: '/templates?tab=report' },
    { path: '/taskbar-templates', redirect: '/templates?tab=taskbar' },
    { path: '/periodic/:type/edit', component: PeriodicEditor },
    { path: '/auth', component: Profiles, props: { kind: 'auth' } },
    { path: '/notifications', component: Notifications },
    { path: '/mail', redirect: '/notifications?tab=mail' },
    { path: '/settings', component: Settings },
    { path: '/storage-section', redirect: '/settings?section=storage' },
    { path: '/security-section', redirect: '/settings?section=security' },
    { path: '/:pathMatch(.*)*', redirect: '/jobs' },
  ],
  scrollBehavior() { return { top: 0 } },
})

createApp(App).component('ActionButton', ActionButton).component('IconButton', IconButton)
  .component('ButtonGroup', ButtonGroup).component('TableText', TableText).use(router).mount('#app')
