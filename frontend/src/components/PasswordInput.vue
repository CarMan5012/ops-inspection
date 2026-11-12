<script setup lang="ts">
import { ref } from 'vue'
import { Eye, EyeOff } from '../icons'

defineOptions({ inheritAttrs: false })

defineProps<{ modelValue?: string | number | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const visible = ref(false)

function update(event: Event) {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <span class="password-input">
    <input
      v-bind="$attrs"
      :type="visible ? 'text' : 'password'"
      :value="modelValue ?? ''"
      @input="update"
    >
    <button
      type="button"
      class="password-input-toggle"
      data-password-toggle
      :aria-label="visible ? '隐藏密码' : '显示密码'"
      :aria-pressed="visible"
      @click.prevent.stop="visible = !visible"
    >
      <Eye v-if="visible" :size="14" />
      <EyeOff v-else :size="14" />
    </button>
  </span>
</template>

<style scoped>
.password-input { display: block; position: relative; width: 100%; }
.password-input-toggle {
  position: absolute;
  top: 50%;
  right: 5px;
  transform: translateY(-50%);
  width: 32px;
  height: 32px;
  min-height: 32px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #7892a0;
  cursor: pointer;
}
.password-input-toggle:hover { background: rgba(40, 119, 145, .09); color: #176b86; }
.password-input-toggle:focus-visible { outline: 2px solid #75aabc; outline-offset: 1px; }
@media (max-width: 700px) {
  .password-input-toggle { width: 36px; height: 36px; min-height: 36px; }
}
</style>
