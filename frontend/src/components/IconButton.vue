<script setup lang="ts">
import { computed, ref, useAttrs, useId } from 'vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'

defineOptions({ inheritAttrs: false })
const props = defineProps<{
  label: string
  to?: RouteLocationRaw
  href?: string
  loading?: boolean
  disabled?: boolean
  toolbar?: boolean
}>()
const attrs = useAttrs()
const tooltipId = useId()
const tooltip = ref<{ x: number; y: number } | null>(null)
function showTooltip(event: Event) {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  tooltip.value = {
    x: Math.max(96, Math.min(window.innerWidth - 96, rect.left + rect.width / 2)),
    y: rect.top < 48 ? rect.bottom + 32 : rect.top - 8,
  }
}
const tag = computed(() => props.to ? RouterLink : props.href ? 'a' : 'button')
const inactive = computed(() => props.disabled || props.loading)
const targetProps = computed(() => props.to ? { to: props.to }
  : props.href ? { href: props.href } : { type: 'button', disabled: inactive.value })
function guard(event: MouseEvent) {
  if (inactive.value) { event.preventDefault(); event.stopImmediatePropagation() }
}
</script>

<template>
  <component :is="tag" v-bind="{ ...attrs, ...targetProps }" class="icon-action"
    :class="toolbar ? 'toolbar-icon-button' : 'table-icon-action'"
    :aria-label="label" :title="undefined" :data-tooltip="label"
    :aria-describedby="tooltip ? tooltipId : undefined"
    :aria-busy="loading || undefined" :aria-disabled="inactive || undefined"
    :tabindex="inactive && tag !== 'button' ? -1 : attrs.tabindex" @click.capture="guard"
    @mouseenter="showTooltip" @mouseleave="tooltip = null" @focus="showTooltip" @blur="tooltip = null">
    <span v-if="loading" class="icon-button-loader" aria-hidden="true"></span>
    <slot v-else />
  </component>
  <Teleport to="body">
    <span v-if="tooltip" :id="tooltipId" role="tooltip" class="icon-action-tooltip"
      :style="{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }">{{ label }}</span>
  </Teleport>
</template>
