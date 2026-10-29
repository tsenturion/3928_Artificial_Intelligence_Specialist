<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import MonitoringView from './MonitoringView.vue'

const isMonitoring = window.location.pathname === '/monitoring'

const categories = [
  { key: 'delivery', name: 'Доставка' },
  { key: 'payment', name: 'Оплата' },
  { key: 'refund', name: 'Возврат' },
  { key: 'technical', name: 'Техническая проблема' },
]

const examples = [
  'Когда привезут мой заказ? Курьер задерживается.',
  'За одну покупку деньги списались два раза.',
  'Не получается войти в личный кабинет.',
]

// Состояние формы, запроса и доступности модели.
const text = ref('')
const touched = ref(false)
const submitting = ref(false)
const result = ref(null)
const requestError = ref('')
const serviceState = ref('checking')
const modelVersion = ref('')
// Вычисляемые значения отвечают за проверку текста и подписи в интерфейсе.
const trimmedText = computed(() => text.value.trim())
const isValid = computed(() => trimmedText.value.length >= 3 && trimmedText.value.length <= 4000 && /\p{L}/u.test(trimmedText.value))
const validationMessage = computed(() => {
  if (!trimmedText.value) return 'Введите текст обращения.'
  if (trimmedText.value.length < 3) return 'Введите не менее 3 символов.'
  if (trimmedText.value.length > 4000) return 'Максимальная длина — 4000 символов.'
  if (!/\p{L}/u.test(trimmedText.value)) return 'Добавьте хотя бы одну букву.'
  return ''
})
const serviceLabel = computed(() => ({
  checking: 'Проверяем сервис',
  ready: 'Сервис готов',
  unavailable: 'Сервис недоступен',
})[serviceState.value])
const percent = (value) => new Intl.NumberFormat('ru-RU', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value ?? 0)

// Изменённый текст делает прежний результат и ошибку неактуальными.
watch(text, () => {
  result.value = null
  requestError.value = ''
  touched.value = false
})

async function checkService() {
  serviceState.value = 'checking'
  modelVersion.value = ''
  try {
    // Проверяем готовность модели перед разрешением отправки.
    const response = await fetch('/api/health', { signal: AbortSignal.timeout(15000) })
    const data = await response.json()
    if (response.ok && data.status === 'ready' && data.model_loaded) {
      serviceState.value = 'ready'
      modelVersion.value = data.model_version || ''
    } else {
      serviceState.value = 'unavailable'
    }
  } catch {
    serviceState.value = 'unavailable'
  }
}

async function submit() {
  touched.value = true
  if (!isValid.value || submitting.value || serviceState.value !== 'ready') return

  const submittedText = trimmedText.value
  submitting.value = true
  result.value = null
  requestError.value = ''
  try {
    // Отправляем только текст обращения; время ожидания ограничено.
    const response = await fetch('/api/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: submittedText }),
      signal: AbortSignal.timeout(15000),
    })
    const data = await response.json().catch(() => null)
    // Запоздалый ответ не должен относиться к уже изменённому тексту.
    if (trimmedText.value !== submittedText) return
    if (response.status === 422) {
      requestError.value = 'Проверьте текст обращения и попробуйте ещё раз.'
    } else if (response.status === 503) {
      serviceState.value = 'unavailable'
      requestError.value = 'Модель сейчас недоступна. Проверьте состояние сервиса позже.'
    } else if (!response.ok || !data?.probabilities || !data?.category) {
      requestError.value = 'Не удалось обработать обращение. Попробуйте ещё раз.'
    } else {
      result.value = data
      modelVersion.value = data.model_version || modelVersion.value
    }
  } catch {
    if (trimmedText.value === submittedText) {
      serviceState.value = 'unavailable'
      requestError.value = 'Нет связи с сервисом. Проверьте подключение и повторите попытку.'
    }
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  if (!isMonitoring) checkService()
})
</script>

<template>
  <div class="min-h-screen">
    <div class="mx-auto max-w-6xl px-5 pb-12 pt-6 sm:px-8 sm:pt-9">
      <header class="flex flex-wrap items-center justify-between gap-4 border-b border-emerald-950/10 pb-6">
        <div class="flex items-center gap-3">
          <div aria-hidden="true" class="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-900 text-lg font-semibold text-white">↗</div>
          <div class="text-sm font-semibold tracking-wide text-emerald-950">Маршрутизация обращений</div>
        </div>
        <nav class="flex items-center gap-4 text-sm" aria-label="Основная навигация">
          <a href="/" :aria-current="!isMonitoring ? 'page' : undefined" class="rounded-md px-2 py-1 font-medium text-emerald-900 hover:bg-emerald-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700" :class="!isMonitoring ? 'bg-emerald-100' : ''">Классификация</a>
          <a href="/monitoring" :aria-current="isMonitoring ? 'page' : undefined" class="rounded-md px-2 py-1 font-medium text-emerald-900 hover:bg-emerald-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700" :class="isMonitoring ? 'bg-emerald-100' : ''">Мониторинг</a>
        </nav>
        <div v-if="!isMonitoring" class="flex flex-wrap items-center gap-3 text-xs sm:text-sm" role="status" aria-live="polite">
          <span class="inline-flex items-center gap-2 rounded-full border px-3 py-1.5 font-medium" :class="serviceState === 'ready' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-stone-200 bg-white text-stone-600'">
            <span aria-hidden="true" class="h-2 w-2 rounded-full" :class="serviceState === 'ready' ? 'bg-emerald-500' : serviceState === 'checking' ? 'bg-amber-400' : 'bg-rose-500'"></span>
            {{ serviceLabel }}
          </span>
          <span v-if="modelVersion" class="text-stone-500">Модель {{ modelVersion }}</span>
          <button v-if="serviceState === 'unavailable'" type="button" class="rounded-md font-medium text-emerald-800 underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700" @click="checkService">Повторить проверку</button>
        </div>
      </header>

      <main v-if="isMonitoring"><MonitoringView /></main>
      <main v-else>
        <div class="max-w-3xl pb-9 pt-12 sm:pb-11 sm:pt-16">
          <div class="mb-4 inline-block rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.12em] text-emerald-800">Помощник поддержки</div>
          <h1 class="text-4xl font-semibold leading-tight tracking-tight text-emerald-950 sm:text-5xl">Узнайте тему обращения</h1>
          <p class="mt-5 max-w-2xl text-base leading-7 text-stone-600 sm:text-lg">Введите сообщение клиента. Сервис определит наиболее подходящую категорию и покажет оценку по каждому направлению.</p>
        </div>

        <div class="grid items-start gap-6 lg:grid-cols-[minmax(0,1.12fr)_minmax(0,0.88fr)]">
          <section class="rounded-2xl border border-stone-200 bg-white p-6 shadow-[0_16px_50px_-38px_rgba(18,44,34,0.45)] sm:p-8" aria-labelledby="input-title">
            <div class="mb-6 flex items-baseline justify-between gap-3">
              <h2 id="input-title" class="text-xl font-semibold tracking-tight text-emerald-950">Новое обращение</h2>
            </div>
            <form @submit.prevent="submit">
              <label for="message" class="mb-2 block text-sm font-semibold text-stone-700">Текст обращения</label>
              <textarea id="message" v-model="text" rows="8" maxlength="4000" placeholder="Например: заказ ещё не доставили, хотя срок уже прошёл…" aria-describedby="message-hint message-error" :aria-invalid="touched && !isValid" class="w-full resize-y rounded-xl border border-stone-300 bg-stone-50/50 px-4 py-3 text-base leading-7 text-stone-900 placeholder:text-stone-400 focus:border-emerald-700 focus:outline-2 focus:outline-offset-1 focus:outline-emerald-700"></textarea>
              <div class="mt-2 flex items-start justify-between gap-4 text-xs text-stone-500">
                <span id="message-hint">От 3 до 4000 символов, хотя бы одна буква.</span>
                <span class="shrink-0 tabular-nums">{{ text.length }} / 4000</span>
              </div>
              <p v-if="touched && !isValid" id="message-error" class="mt-3 text-sm font-medium text-rose-700" role="alert">{{ validationMessage }}</p>
              <p v-if="requestError" class="mt-3 text-sm font-medium text-rose-700" role="alert">{{ requestError }}</p>

              <div class="mt-7">
                <p class="mb-3 text-xs font-semibold uppercase tracking-[0.1em] text-stone-500">Попробуйте пример</p>
                <div class="flex flex-wrap gap-2">
                  <button v-for="example in examples" :key="example" type="button" class="rounded-lg border border-stone-200 bg-white px-3 py-2 text-left text-xs leading-5 text-stone-600 transition hover:border-emerald-300 hover:bg-emerald-50 hover:text-emerald-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700" @click="text = example">{{ example }}</button>
                </div>
              </div>
              <button type="submit" :disabled="submitting || serviceState !== 'ready'" class="mt-8 inline-flex min-h-12 w-full items-center justify-center rounded-xl bg-emerald-800 px-5 py-3 text-sm font-semibold text-white transition hover:bg-emerald-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700 disabled:cursor-not-allowed disabled:bg-stone-300 disabled:text-stone-600 sm:w-auto sm:min-w-48">
                {{ submitting ? 'Определяем тему…' : 'Определить категорию' }}
              </button>
            </form>
          </section>

          <section class="rounded-2xl border border-stone-200 bg-white p-6 shadow-[0_16px_50px_-38px_rgba(18,44,34,0.45)] sm:p-8" aria-labelledby="result-title" aria-live="polite">
            <h2 id="result-title" class="text-xl font-semibold tracking-tight text-emerald-950">Результат</h2>
            <div v-if="result" class="mt-7">
              <p class="text-xs font-semibold uppercase tracking-[0.12em] text-stone-500">Наиболее вероятная категория</p>
              <p class="mt-2 text-3xl font-semibold tracking-tight text-emerald-900">{{ result.category_name || categories.find((item) => item.key === result.category)?.name }}</p>
              <p class="mt-2 text-sm text-stone-500">Оценка модели: {{ percent(result.probability) }}</p>
              <div class="mt-8 space-y-5 border-t border-stone-100 pt-7">
                <div v-for="item in categories" :key="item.key">
                  <div class="mb-2 flex justify-between gap-3 text-sm"><span class="text-stone-700">{{ item.name }}</span><span class="font-semibold tabular-nums text-stone-800">{{ percent(result.probabilities[item.key]) }}</span></div>
                  <div class="h-2 overflow-hidden rounded-full bg-stone-100"><div class="h-full rounded-full" :class="item.key === result.category ? 'bg-emerald-700' : 'bg-emerald-300'" :style="{ width: `${Math.max(0, Math.min(100, (result.probabilities[item.key] ?? 0) * 100))}%` }"></div></div>
                </div>
              </div>
              <p class="mt-8 text-xs leading-5 text-stone-500">Модель обучена на синтетических обращениях. Оценка вероятности не гарантирует правильный ответ.</p>
            </div>
            <div v-else class="mt-7 flex min-h-64 flex-col items-start justify-center rounded-xl border border-dashed border-stone-200 bg-stone-50 px-6 py-8">
              <div aria-hidden="true" class="mb-5 flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-100 text-xl text-emerald-800">◎</div>
              <p class="font-medium text-stone-700">{{ submitting ? 'Анализируем обращение…' : 'Здесь появится результат' }}</p>
              <p class="mt-2 max-w-xs text-sm leading-6 text-stone-500">{{ submitting ? 'Обычно это занимает несколько секунд.' : 'Введите текст и нажмите «Определить категорию».' }}</p>
            </div>
          </section>
        </div>
      </main>
    </div>
  </div>
</template>
