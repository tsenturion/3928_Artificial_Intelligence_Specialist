<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

const windowHours = ref(24)
const report = ref(null)
const loading = ref(false)
const error = ref('')
let activeRequest = null
let requestNumber = 0

const integer = (value) => new Intl.NumberFormat('ru-RU').format(value ?? 0)
const percent = (value) => value == null ? '—' : new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 1 }).format(value)
const decimal = (value) => value == null ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value)
const dateTime = (value) => value ? new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
const barWidth = (value) => `${Math.max(0, Math.min(100, (value ?? 0) * 100))}%`

const dataStatus = {
  insufficient_data: 'Недостаточно данных для оценки',
  no_reference: 'Нет данных для сравнения',
  warning: 'Есть признаки изменения входных данных',
  ok: 'Заметных изменений не обнаружено',
}

async function loadMonitoring() {
  // Отменяем предыдущий запрос, чтобы смена окна не показала устаревшие данные.
  activeRequest?.abort()
  const controller = new AbortController()
  activeRequest = controller
  const currentRequest = ++requestNumber
  let timedOut = false
  const timeout = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, 15000)
  report.value = null
  error.value = ''
  loading.value = true

  try {
    const response = await fetch(`/api/monitoring?hours=${windowHours.value}`, { signal: controller.signal })
    const data = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = typeof data?.detail === 'string' ? ` ${data.detail}` : ''
      throw new Error(response.status === 503
        ? `Мониторинг временно недоступен.${detail}`
        : `Не удалось получить данные мониторинга.${detail}`)
    }
    if (!data?.traffic || !data?.predictions || !data?.data || !data?.quality) {
      throw new Error('Сервис вернул неполные данные мониторинга.')
    }
    if (currentRequest === requestNumber) report.value = data
  } catch (cause) {
    if (currentRequest === requestNumber) {
      error.value = timedOut
        ? 'Сервис не ответил за 15 секунд. Повторите попытку.'
        : controller.signal.aborted
          ? ''
          : cause instanceof TypeError ? 'Нет связи с сервисом. Повторите попытку.' : cause.message
    }
  } finally {
    clearTimeout(timeout)
    if (currentRequest === requestNumber) {
      loading.value = false
      activeRequest = null
    }
  }
}

onMounted(loadMonitoring)
onUnmounted(() => activeRequest?.abort())
</script>

<template>
  <div class="pb-10 pt-10 sm:pt-14">
    <div class="flex flex-wrap items-end justify-between gap-5">
      <div>
        <div class="mb-4 inline-block rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.12em] text-emerald-800">Состояние сервиса</div>
        <h1 class="text-4xl font-semibold tracking-tight text-emerald-950 sm:text-5xl">Мониторинг</h1>
        <p class="mt-4 max-w-2xl text-sm leading-6 text-stone-600 sm:text-base">Запросы и ответы сервиса за выбранный период. Показатели модели относятся к текущему выпуску.</p>
      </div>
      <div class="flex flex-wrap items-end gap-3">
        <div>
          <label for="monitoring-window" class="mb-1 block text-sm font-medium text-stone-700">Период</label>
          <select id="monitoring-window" v-model.number="windowHours" class="h-11 rounded-lg border border-stone-300 bg-white px-3 text-sm text-stone-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700" @change="loadMonitoring">
            <option :value="24">24 часа</option>
            <option :value="168">7 дней</option>
          </select>
        </div>
        <button type="button" :disabled="loading" class="h-11 rounded-lg bg-emerald-800 px-4 text-sm font-semibold text-white hover:bg-emerald-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700 disabled:cursor-wait disabled:bg-stone-400" @click="loadMonitoring">{{ loading ? 'Обновляем…' : 'Обновить' }}</button>
      </div>
    </div>

    <div class="mt-7" aria-live="polite">
      <p v-if="loading" class="rounded-xl border border-stone-200 bg-white p-6 text-stone-600" role="status">Загружаем показатели…</p>
      <div v-else-if="error" class="rounded-xl border border-rose-200 bg-rose-50 p-6" role="alert">
        <p class="text-sm font-medium text-rose-800">{{ error }}</p>
        <button type="button" class="mt-3 rounded-md text-sm font-semibold text-emerald-800 underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700" @click="loadMonitoring">Повторить</button>
      </div>
      <template v-else-if="report">
        <p class="text-xs text-stone-500">Обновлено: {{ dateTime(report.generated_at) }}<span v-if="report.model"> · Модель {{ report.model.model_version }} · Данные {{ report.model.data_version }}</span></p>

        <section class="mt-6" aria-labelledby="traffic-title">
          <h2 id="traffic-title" class="text-xl font-semibold text-emerald-950">Запросы к классификации</h2>
          <p class="mt-1 text-sm text-stone-500">Учитываются все попытки классификации, включая ошибки.</p>
          <div class="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div class="rounded-2xl border border-stone-200 bg-white p-5"><p class="text-sm text-stone-500">Запросов</p><p class="mt-2 text-3xl font-semibold tabular-nums text-emerald-950">{{ integer(report.traffic.requests) }}</p></div>
            <div class="rounded-2xl border border-stone-200 bg-white p-5"><p class="text-sm text-stone-500">Доля ошибок</p><p class="mt-2 text-3xl font-semibold tabular-nums text-emerald-950">{{ percent(report.traffic.error_rate) }}</p><p class="mt-2 text-xs text-stone-500">Ошибки запроса: {{ integer(report.traffic.client_errors) }} · Ошибки сервиса: {{ integer(report.traffic.server_errors) }}</p></div>
            <div class="rounded-2xl border border-stone-200 bg-white p-5"><p class="text-sm text-stone-500">Средняя задержка</p><p class="mt-2 text-3xl font-semibold tabular-nums text-emerald-950">{{ decimal(report.traffic.avg_latency_ms) }}<span v-if="report.traffic.avg_latency_ms != null" class="ml-1 text-base font-normal">мс</span></p></div>
            <div class="rounded-2xl border border-stone-200 bg-white p-5"><p class="text-sm text-stone-500">Задержка p95</p><p class="mt-2 text-3xl font-semibold tabular-nums text-emerald-950">{{ decimal(report.traffic.p95_latency_ms) }}<span v-if="report.traffic.p95_latency_ms != null" class="ml-1 text-base font-normal">мс</span></p></div>
          </div>
        </section>

        <div class="mt-6 grid gap-6 lg:grid-cols-2">
          <section class="rounded-2xl border border-stone-200 bg-white p-6" aria-labelledby="categories-title">
            <h2 id="categories-title" class="text-xl font-semibold text-emerald-950">Категории обращений</h2>
            <p class="mt-1 text-sm text-stone-500">{{ integer(report.predictions.total) }} успешных классификаций текущей модели. Сравнение — с обучающими данными.</p>
            <div v-if="report.predictions.total" class="mt-6 space-y-5">
              <div v-for="category in report.predictions.categories" :key="category.category">
                <div class="mb-2 flex justify-between gap-3 text-sm"><span class="text-stone-700">{{ category.name }}</span><span class="font-semibold tabular-nums text-stone-800">{{ integer(category.count) }} · {{ percent(category.share) }}</span></div>
                <div class="h-2 overflow-hidden rounded-full bg-stone-100" role="img" :aria-label="`${category.name}: ${percent(category.share)}`"><div class="h-full rounded-full bg-emerald-700" :style="{ width: barWidth(category.share) }"></div></div>
                <p v-if="category.reference_share != null" class="mt-1 text-xs text-stone-500">В обучении: {{ percent(category.reference_share) }}</p>
              </div>
            </div>
            <p v-else class="mt-6 text-sm text-stone-500">Классификаций за этот период пока нет.</p>
          </section>

          <section class="rounded-2xl border border-stone-200 bg-white p-6" aria-labelledby="data-title">
            <h2 id="data-title" class="text-xl font-semibold text-emerald-950">Изменение входных данных</h2>
            <p class="mt-2 text-sm font-medium" :class="report.data.status === 'warning' ? 'text-amber-800' : 'text-stone-600'">{{ dataStatus[report.data.status] || 'Оценка недоступна' }}</p>
            <p class="mt-2 text-sm text-stone-500">Проверено обращений: {{ integer(report.data.sample_count) }}<span v-if="report.data.status === 'insufficient_data'">; для оценки нужно не менее {{ integer(report.data.min_samples) }}</span>.</p>
            <dl class="mt-6 grid gap-4 sm:grid-cols-2">
              <div><dt class="text-sm text-stone-500">Средняя длина текста</dt><dd class="mt-1 font-semibold tabular-nums text-emerald-950">{{ decimal(report.data.avg_length) }}<span v-if="report.data.avg_length != null" class="font-normal"> символов</span></dd><p v-if="report.data.reference_avg_length != null" class="text-xs text-stone-500">В обучении: {{ decimal(report.data.reference_avg_length) }}</p></div>
              <div><dt class="text-sm text-stone-500">Известные модели фрагменты</dt><dd class="mt-1 font-semibold tabular-nums text-emerald-950">{{ percent(report.data.avg_coverage) }}</dd><p v-if="report.data.reference_avg_coverage != null" class="text-xs text-stone-500">В обучении: {{ percent(report.data.reference_avg_coverage) }}</p></div>
            </dl>
            <ul v-if="report.data.warnings?.length" class="mt-5 list-disc space-y-1 pl-5 text-sm text-amber-800"><li v-for="warning in report.data.warnings" :key="warning">{{ warning }}</li></ul>
            <p class="mt-6 border-t border-stone-100 pt-4 text-xs leading-5 text-stone-500">Это ориентир по изменению обращений, а не подтверждение снижения качества. Тексты обращений здесь не сохраняются.</p>
          </section>
        </div>

        <section class="mt-6 rounded-2xl border border-stone-200 bg-white p-6" aria-labelledby="quality-title">
          <h2 id="quality-title" class="text-xl font-semibold text-emerald-950">Качество на размеченных обращениях</h2>
          <p class="mt-1 text-sm text-stone-500">Оценка учитывает только обращения, для которых отдельно указана правильная категория.</p>
          <div class="mt-5 grid gap-5 sm:grid-cols-3">
            <div><p class="text-sm text-stone-500">Размечено</p><p class="mt-1 text-2xl font-semibold tabular-nums text-emerald-950">{{ integer(report.quality.labeled_count) }}</p></div>
            <div><p class="text-sm text-stone-500">Точность</p><p class="mt-1 text-2xl font-semibold tabular-nums text-emerald-950">{{ report.quality.labeled_count ? percent(report.quality.accuracy) : '—' }}</p></div>
            <div><p class="text-sm text-stone-500">Macro F1</p><p class="mt-1 text-2xl font-semibold tabular-nums text-emerald-950">{{ report.quality.labeled_count ? percent(report.quality.macro_f1) : '—' }}</p></div>
          </div>
          <p v-if="!report.quality.labeled_count" class="mt-4 text-sm text-stone-500">Разметки пока нет, поэтому качество оценить нельзя.</p>
          <p v-else class="mt-4 text-xs leading-5 text-stone-500">Категорий с разметкой: {{ integer(report.quality.category_coverage) }} из 4. Macro F1 учитывает все четыре категории; для отсутствующих в разметке используется F1 = 0.</p>
        </section>
      </template>
    </div>
  </div>
</template>
