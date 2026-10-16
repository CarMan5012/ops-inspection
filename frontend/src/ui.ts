import { reactive } from 'vue'

export const notice = reactive({ message: '', kind: 'success' as 'success' | 'error' | 'info' })
let timer: number | undefined

export function notify(message: string, kind: 'success' | 'error' | 'info' = 'success'): void {
  notice.message = message
  notice.kind = kind
  clearTimeout(timer)
  timer = window.setTimeout(() => { notice.message = '' }, kind === 'error' ? 8500 : 4500)
}

export async function attempt<T>(task: () => Promise<T>, success?: string): Promise<T | undefined> {
  try {
    const result = await task()
    if (success) notify(success)
    return result
  } catch (error) {
    notify(error instanceof Error ? error.message : '操作失败，请稍后重试', 'error')
    return undefined
  }
}

export interface ConfirmOptions {
  title?: string
  message: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}

export const confirmState = reactive({
  open: false,
  title: '操作确认',
  message: '',
  confirmText: '确定',
  cancelText: '取消',
  danger: false,
  resolve: null as ((value: boolean) => void) | null,
})

export function confirmModal(options: string | ConfirmOptions): Promise<boolean> {
  if (confirmState.open) return Promise.resolve(false)
  const opt: ConfirmOptions = typeof options === 'string' ? { message: options } : options
  confirmState.title = opt.title || '操作确认'
  confirmState.message = opt.message
  confirmState.confirmText = opt.confirmText || '确定'
  confirmState.cancelText = opt.cancelText || '取消'
  confirmState.danger = Boolean(opt.danger)
  confirmState.open = true
  return new Promise<boolean>((resolve) => {
    confirmState.resolve = resolve
  })
}

export function handleConfirm(val: boolean): void {
  confirmState.open = false
  if (confirmState.resolve) {
    confirmState.resolve(val)
    confirmState.resolve = null
  }
}
