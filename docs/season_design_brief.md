# Бриф для редизайна страницы «Распродажа сезона»

Цель сессии: сделать страницу **красивее**, не сломав логику. Это самодостаточный
документ — по нему можно переоформить интерфейс, не читая весь код.

---

## 1. Что это за страница

- **Файл:** `season_page.html` (один файл: HTML + CSS + ванильный JS, без сборки/фреймворков).
- **Назначение:** аналитический дашборд распродажи сезонной категории Wildberries (по умолчанию «Шорты»).
  Показывает остатки, темп продаж, % выкупа, «на сколько хватит», сколько распродать к концу сезона,
  рекомендованную скидку и цену, разбивку по размерам.
- **Аудитория:** владелец магазина и менеджер по продажам (не технари). Нужна наглядность и «с первого взгляда понятно, что делать».
- **Бренд:** JOTO — молодёжный streetwear. Фирменный акцент — кислотно-лаймовый `#CCFF00`.
- **Открывается:** напрямую `/season` и встраивается в Bitrix24 (iframe в левом меню). Есть тёмная/светлая тема.

## 2. Как запустить и посмотреть (без токена WB)

Открыть `season_page.html` в браузере и нажать **«Сформировать»** — если WB-токена нет,
страница покажет **демо-данные** (генерятся в JS-функции `demoReport()`), так что весь интерфейс
виден без бэкенда. Это идеально для дизайна: можно править вёрстку и сразу смотреть на демо.

## 3. Текущая структура (порядок блоков сверху вниз)

1. **Шапка** (`.head`): вордмарк `joto` (`.wordmark-text`, италик-жирный), ссылка «← к артикулам» (`.back`),
   заголовок-сериф «Распродажа сезона» (`#catTitle` дописывает «— Шорты»), подпись-описание (`.screen-sub`).
2. **Панель настроек** (`.controls`): селекты/инпуты — Категория (`#category`), Артикул-фильтр (`#artFilter`),
   Конец сезона (`#seasonEnd`), Оставить % (`#targetPct`), Период с/по (`#periodStart`/`#periodEnd`),
   кнопка **Сформировать** (`#run`). Бейдж «демо-данные» (`#demo`).
3. **Строка статуса** (`#note`) + **панель обновления** (`#updbar`): «🟢 Данные WB обновлены: HH:MM · X назад»
   (`#updinfo`) и галка «автообновление каждые 5 мин» (`#autorefresh`).
4. **`#report`** (скрыт до первого расчёта, это flex-column с заданным порядком):
   - **Вывод и план** (`.verdict`) — текстовый итог-рекомендация (`#verdict`).
   - **KPI-карточки** (`.kpis` → `.kpi`) — ~11 карточек: начальный остаток, остаток (общее), выкуп, темп,
     хватит, до конца сезона, распродать, прогноз неликвида, скидка, рекоменд. цена, потолок по марже.
   - **Таблица артикулов** (`#postable`) — широкая таблица, 17 столбцов, первый столбец закреплён (sticky),
     внизу строка ИТОГО (`#tfoot .totalrow`). Клик по строке раскрывает мини-таблицу по размерам.
   - **Аналитика по размерам** (`#sizeblock`) — таблица по размерам категории (`#sizetbody`).
   - **Сценарии по цене** (`#scenblock`) — таблица «если поднять скидку» (`#scentbody`).
   - **Методика** (`#methodology`) — мелкая сноска-пояснение.
5. **Кнопка темы** (`#theme-toggle`, ☾/☀) — фиксированная.

## 4. Текущие дизайн-токены (CSS-переменные в `:root` и темах)

```
Акцент:    --accent #CCFF00 (лайм), --accent-ink #0A0A0A, --accent-deep #9bc400
Статусы:   --ok #2e9e5b, --warn #d98a00, --bad #e0483a
Шрифты:    --serif (Georgia, для заголовков), --sans (system-ui), --mono (для цифр KPI)
Радиус:    --r 14px;  Тайминг: --ease cubic-bezier(.2,.7,.2,1)

Светлая:  bg #F1F1EE, surface #FFFFFF, surface-2 #F7F7F4, ink #0B0B0C,
          ink-2 #56564F, ink-3 #8E8E86, line #E4E4DE
Тёмная:   bg #0A0A0B, surface #141416, surface-2 #1B1B1E, ink #F3F3EE, line #27272B
```
Стиль сейчас: editorial / минимализм — серифные италик-заголовки, моноширинные цифры,
тонкие рамки, мягкие тени, лаймовый акцент точечно. Тему хочется сохранить (это бренд),
но можно сделать выразительнее: иерархия, «крупные» ключевые цифры, цветовое кодирование статусов,
аккуратные карточки/таблицы, лучше для мобильного.

## 5. Идеи «красивее» (направление, не жёсткое ТЗ)

- Сильнее выделить **3–4 главные цифры** (сколько распродать, нужный темп, рекоменд. скидка/цена) — герой-блок.
- Цветовое кодирование статусов строк: `ok`/`accelerate`/`stuck`/`empty` (бейджи `.pill`).
- Аккуратнее широкая таблица: зебра, плотность, читаемые заголовки, sticky-столбец с тенью.
- Микро-визуализации: прогресс-бар «остаток vs цель», полоска выкупа, стрелки динамики.
- Адаптив: на телефоне таблица тяжёлая — продумать карточный вид строк или горизонтальный скролл с подсказкой.
- Сохранить тёмную/светлую тему и лаймовый акцент.

## 6. Что НЕЛЬЗЯ ломать (JS завязан на эти id/классы/структуру)

JS ищет элементы по id и строит таблицы программно. При редизайне **сохранить эти хуки**
(можно менять стили/обёртки, но id и порядок колонок — оставить):

- **id элементов:** `category, artFilter, seasonEnd, targetPct, periodStart, periodEnd, run, demo, note,
  updbar, updinfo, autorefresh, report, verdict, kpis, catTitle, sizehint, postable, tbody, tfoot,
  sizeblock, sizetbody, scenblock, scentbody, methodology, theme-toggle`.
- **Таблица артикулов `#postable`:** 17 колонок в порядке —
  `Артикул, Нач.остаток, Остаток(на ВБ), Общее, К клиенту, От клиента, Заказов/день, Динамика,
  Хватит до, Прогноз остатка, Нужно/день, В неликвид, Скидка, Цена сейчас, Реком.цена, Мин.цена, Статус`.
  Строки и строка ИТОГО (`#tfoot`) генерятся в JS — кол-во `<td>` должно совпадать с числом `<th>` (17).
  Деталь-строка размеров использует `colspan="17"`.
- **Классы-хуки:** `.kpi/.k/.v/.s` (карточки; `.v.up/.v.down` — зелёный/красный),
  `.pill` (+ `ok/accelerate/stuck/empty`), `.art`, `.caret`, `.szbtn`, `.sizedetail`, `.sizemini`,
  `.recprice`, `.totalrow`, `.filtered-out` (скрытие строк фильтром), `.updbar .dot`.
- **Первый столбец закреплён** через `position:sticky` на `#postable th/td:first-child` — сохранить.
- **Темизация** через `html[data-theme="light|dark"]` и CSS-переменные — сохранить переключатель `#theme-toggle`.
- **Демо-режим**: функция `demoReport()` отдаёт те же поля, что бэкенд (см. ниже). Не удалять.

## 7. Форма данных (что приходит и что рисовать)

Эндпоинт `GET /api/wb/season-report` → `{ ok:true, report:{...} }`. Поля `report`:

**Верх:** `title, generatedAt, dataUpdatedAt, dataUpdatedIso, seasonEnd, daysLeft, lookbackDays,
periodStart, periodEnd, targetRemainPct, count`.

**`report.summary`** (для KPI/вывода/сценариев/размеров):
`totalStock` (на ВБ), `totalFull` (общее — база расчётов), `initialStock` (начальный приход),
`soldSinceStart`, `soldRecent` (заказы за окно), `salesRecent` (выкупы за окно), `buyoutPct` (% выкупа, 60 дн),
`currentDaily`, `trendPct`, `daysOfSupply`, `depletionDate`, `requiredDaily`, `needSell` (сколько распродать),
`projLeft`, `projLeftPct`, `targetLeftUnits`, `targetFromInitial`, `deadstock`,
`currentDiscount`, `recommendedDiscount`, `currentPrice`, `recommendedPrice`, `minPrice`,
`maxDiscountByMargin`, `marginLimited`, `costSharePct`, `minMarginPct`, `pricesAvailable`,
`scenarios:[{addDiscount,discount,dailyRate,daysToTarget,daysSaved,selloutDate,projLeftPct,hitsTarget}]`,
`sizes:[{size,stock,initialStock,soldSinceStart,dailyRate,daysOfSupply,projLeftPct,status,
currentDiscount,recommendedDiscount,currentPrice,recommendedPrice,minPrice}]`,
`verdict` (готовый текст рекомендации).

**`report.rows[]`** (строки таблицы артикулов):
`nmId, vendorCode, subject, initialStock, soldSinceStart, stock, stockFull, toClient, fromClient,
soldRecent, salesRecent, buyoutPct, dailyRate, trendPct, daysOfSupply, depletionDate,
projLeft, projLeftPct, requiredDaily, deadstock, currentDiscount, recommendedDiscount,
currentPrice, recommendedPrice, minPrice, status (ok|accelerate|stuck|empty), sizes:[ ...как summary.sizes ]`.

Полный реальный пример лежит в `docs/season_sample_report.json`.

## 8. Договорённости по смыслу (чтобы подписи были верными)

- Все расчёты — от **общего** количества (`totalFull`/`stockFull`), темп — **чистый** (заказы × % выкупа за 60 дн).
- Цель «оставить ≤10%» — от **начального остатка** (приходы с производств).
- «Остаток на ВБ» (`quantity`) и «К клиенту/От клиента» — справочные колонки.
- Цена: `currentPrice` (сейчас), `recommendedPrice` (под нужный темп), `minPrice` (предел по марже).

## 9. Как вернуть результат

Редактировать только `season_page.html` (при желании вынести CSS — но проект ожидает один файл).
Проверка: открыть в браузере, «Сформировать» → демо-данные; переключить тему; сузить окно (адаптив);
кликнуть по строке артикула (раскрытие размеров); выбрать артикул в фильтре (пересчёт KPI).
JS не трогать, кроме мест, где это нужно для новой вёрстки (сохранить id/классы/кол-во колонок).
