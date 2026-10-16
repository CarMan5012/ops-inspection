<script setup lang="ts">
import { computed, ref, useAttrs } from 'vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'

defineOptions({ inheritAttrs: false })
const props = withDefaults(defineProps<{
  variant?: 'primary' | 'secondary' | 'danger' | 'quiet'
  to?: RouteLocationRaw
  href?: string
  type?: 'button' | 'submit' | 'reset'
  loading?: boolean
  disabled?: boolean
}>(), { variant: 'secondary', type: 'button' })
const attrs = useAttrs()
const element = ref<HTMLElement | { $el: HTMLElement } | null>(null)
defineExpose({ focus: () => {
  const root = element.value
  if (root) ('focus' in root ? root : root.$el).focus()
} })
const tag = computed(() => props.to ? RouterLink : props.href ? 'a' : 'button')
const inactive = computed(() => props.disabled || props.loading)
const targetProps = computed(() => props.to
  ? { to: props.to }
  : props.href ? { href: props.href } : { type: props.type, disabled: inactive.value })
function guard(event: MouseEvent) {
  if (inactive.value) { event.preventDefault(); event.stopImmediatePropagation() }
}
</script>

<template>
  <component ref="element" :is="tag" v-bind="{ ...attrs, ...targetProps }"
    class="btn action-button" :class="[`btn-${variant}`, { 'is-loading': loading }]"
    :aria-busy="loading || undefined" :aria-disabled="inactive || undefined"
    :tabindex="inactive && tag !== 'button' ? -1 : attrs.tabindex"
    @click.capture="guard">
    <span v-if="loading" class="action-button-loader" aria-hidden="true"></span>
    <span class="action-button-label"><slot /></span>
  </component>
</template>
