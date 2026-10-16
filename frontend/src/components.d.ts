import ActionButton from './components/ActionButton.vue'
import IconButton from './components/IconButton.vue'
import ButtonGroup from './components/ButtonGroup.vue'
import TableText from './components/TableText.vue'

declare module 'vue' {
  export interface GlobalComponents {
    ActionButton: typeof ActionButton
    IconButton: typeof IconButton
    ButtonGroup: typeof ButtonGroup
    TableText: typeof TableText
  }
}
export {}
