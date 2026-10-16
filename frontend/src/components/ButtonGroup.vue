<script setup lang="ts">
import { Fragment, cloneVNode, defineComponent, h, isVNode, type VNode } from 'vue'

// Normalize direct actions, including conditional fragments, to one primary action.
const GroupContent = defineComponent({
  setup(_, { slots }) {
    return () => {
      let primarySeen = false
      const normalize = (nodes: VNode[]): VNode[] => nodes.map(node => {
        if (node.type === Fragment && Array.isArray(node.children)) {
          return h(Fragment, node.props, normalize(node.children.filter(isVNode)))
        }
        if (node.props?.variant === 'primary') {
          if (primarySeen) return cloneVNode(node, { variant: 'secondary' })
          primarySeen = true
        }
        return node
      })
      return normalize(slots.default?.() || [])
    }
  },
})
</script>

<template><div class="button-group"><GroupContent><slot /></GroupContent></div></template>
