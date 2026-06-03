import os
import re
import time
import mimetypes
import threading
import schedule
import httpx
import psycopg2
from flask import Flask, request, jsonify, Response
from datetime import datetime, timedelta

import nk_integration as nk  # интеграция с Национальным каталогом (Честный ЗНАК)

app = Flask(__name__)

# ===================== КОНФИГ (переменные окружения) =====================

WB_API_TOKEN = os.environ.get("WB_API_TOKEN", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# Bitrix24 — локальное приложение (OAuth)
BITRIX_CLIENT_ID = os.environ.get("BITRIX_CLIENT_ID", "").strip()
BITRIX_CLIENT_SECRET = os.environ.get("BITRIX_CLIENT_SECRET", "").strip()
BITRIX_APP_TOKEN = os.environ.get("BITRIX_APP_TOKEN", "").strip()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

# Кому слать отчёт по задачам и алерты CTR
REPORT_USER_ID = os.environ.get("REPORT_USER_ID", "226").strip()
CTR_ALERT_DIALOG = os.environ.get("CTR_ALERT_DIALOG", "chat2024").strip()

# Ссылка на логотип (необязательно). Приоритет выше файла logo.* в репозитории.
LOGO_URL = os.environ.get("LOGO_URL", "").strip()

# Имя бота, которое увидят пользователи в Битриксе
BOT_NAME = "Article Generator"
BOT_CODE = "joto_article_bot"

EVENT_HANDLER_URL = f"{PUBLIC_BASE_URL}/bitrix/events" if PUBLIC_BASE_URL else ""
# Пункт левого меню Bitrix24 → открывает раздел массовых карточек WB
LEFT_MENU_HANDLER_URL = f"{PUBLIC_BASE_URL}/cards" if PUBLIC_BASE_URL else ""
LEFT_MENU_TITLE = "Карточки WB"

# Встроенные категории по инструкции JOTO (включая словоформы для чата)
CATEGORIES = {
    "жилет": "01", "жилеты": "01",
    "куртка": "02", "куртки": "02",
    "водолазка": "03", "водолазки": "03",
    "джинсы": "04",
    "худи": "05",
    "свитер": "06", "свитера": "06",
    "лонгслив": "07", "лонгсливы": "07",
    "брюки": "09",
    "шорты": "10",
    "футболка": "11", "футболки": "11",
}

CATS_LIST = "жилет, куртка, водолазка, джинсы, худи, свитер, лонгслив, брюки, шорты, футболка"

user_states = {}

# ===================== БД =====================

def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS model_counters (
                category_code VARCHAR(10) PRIMARY KEY,
                counter INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bitrix_oauth (
                id           INTEGER PRIMARY KEY,
                access_token TEXT,
                refresh_token TEXT,
                expires_at   BIGINT,
                domain       TEXT,
                member_id    TEXT,
                bot_id       TEXT,
                app_token    TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY,
                code VARCHAR(2) NOT NULL UNIQUE
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("БД инициализирована")
    except Exception as e:
        print(f"Ошибка БД: {e}")

# ===================== КАТЕГОРИИ (встроенные + добавленные) =====================

CATEGORY_TITLES = {
    "жилет": "Жилет", "куртка": "Куртка", "водолазка": "Водолазка",
    "джинсы": "Джинсы", "худи": "Худи", "свитер": "Свитер",
    "лонгслив": "Лонгслив", "брюки": "Брюки", "шорты": "Шорты", "футболка": "Футболка",
}

def db_list_categories():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT name, code FROM categories ORDER BY code")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [(r[0], r[1]) for r in rows]
    except Exception as e:
        print(f"Ошибка db_list_categories: {e}")
        return []

def resolve_category_code(name):
    name = (name or "").strip().lower()
    if name in CATEGORIES:
        return CATEGORIES[name]
    for n, code in db_list_categories():
        if n.lower() == name:
            return code
    return None

def all_categories():
    seen_codes = set()
    items = []
    for value, title in CATEGORY_TITLES.items():
        code = CATEGORIES.get(value)
        if code and code not in seen_codes:
            items.append({"value": value, "title": title, "code": code})
            seen_codes.add(code)
    for name, code in db_list_categories():
        if code not in seen_codes:
            items.append({"value": name, "title": name.capitalize(), "code": code})
            seen_codes.add(code)
    items.sort(key=lambda x: x["code"])
    return items

def used_category_codes():
    codes = set(CATEGORIES.values())
    for _, code in db_list_categories():
        codes.add(code)
    return codes

def next_free_category_code():
    used = used_category_codes()
    for i in range(1, 100):
        c = str(i).zfill(2)
        if c not in used:
            return c
    return None

def add_category(name, code=None):
    name = (name or "").strip().lower()
    if not name:
        return False, "Укажите название категории"
    if resolve_category_code(name):
        return False, "Такая категория уже есть"
    if code:
        code = str(code).strip()
        if not re.fullmatch(r"\d{2}", code):
            return False, "Код должен быть из 2 цифр"
        if code in used_category_codes():
            return False, "Этот код уже занят"
    else:
        code = next_free_category_code()
        if not code:
            return False, "Свободных кодов не осталось"
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO categories (name, code) VALUES (%s, %s)", (name, code))
        conn.commit()
        cur.close()
        conn.close()
        return True, code
    except Exception as e:
        print(f"Ошибка add_category: {e}")
        return False, "Ошибка сохранения"

# ===================== СЧЁТЧИКИ НОМЕРОВ =====================

def get_next_model_number(category_code):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO model_counters (category_code, counter)
            VALUES (%s, 1)
            ON CONFLICT (category_code) DO UPDATE
            SET counter = model_counters.counter + 1
            RETURNING counter
        """, (category_code,))
        number = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return str(number).zfill(3)
    except Exception as e:
        print(f"Ошибка счётчика: {e}")
        return "001"

def get_current_counter(category_code):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT counter FROM model_counters WHERE category_code=%s", (category_code,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        return 0

def set_counter(category_code, value):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO model_counters (category_code, counter)
            VALUES (%s, %s)
            ON CONFLICT (category_code) DO UPDATE SET counter = EXCLUDED.counter
        """, (category_code, value))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Ошибка set_counter: {e}")

# ===================== СИНХРОНИЗАЦИЯ С WB (уникальность артикулов) =====================

ARTICLE_RE = re.compile(r"^J(\d{2})(\d{3})", re.IGNORECASE)

_wb_used_cache = {"ts": 0.0, "used": {}}
_WB_CACHE_TTL = 300

def fetch_wb_vendor_codes():
    codes = []
    if not WB_API_TOKEN:
        return codes
    url = "https://content-api.wildberries.ru/content/v2/get/cards/list"
    headers = {"Authorization": WB_API_TOKEN}
    cursor = {"limit": 100}
    for _ in range(200):
        payload = {"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}}
        resp = httpx.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            print(f"WB Content API {resp.status_code}: {resp.text[:200]}")
            break
        data = resp.json()
        cards = data.get("cards", []) or []
        for c in cards:
            vc = c.get("vendorCode")
            if vc:
                codes.append(vc)
        cur = data.get("cursor", {}) or {}
        total = cur.get("total", 0)
        if total < cursor["limit"]:
            break
        cursor = {"limit": 100, "updatedAt": cur.get("updatedAt"), "nmID": cur.get("nmID")}
    return codes

def _used_numbers_by_code(codes):
    used = {}
    for vc in codes:
        m = ARTICLE_RE.match((vc or "").strip())
        if m:
            cc = m.group(1)
            nn = int(m.group(2))
            used.setdefault(cc, set()).add(nn)
    return used

def get_used_numbers(category_code):
    now = time.time()
    if now - _wb_used_cache["ts"] > _WB_CACHE_TTL or not _wb_used_cache["used"]:
        try:
            codes = fetch_wb_vendor_codes()
            if codes:
                _wb_used_cache["used"] = _used_numbers_by_code(codes)
                _wb_used_cache["ts"] = now
                print(f"WB: загружено артикулов {len(codes)}")
        except Exception as e:
            print(f"Ошибка синхронизации с WB: {e}")
    return _wb_used_cache["used"].get(category_code, set())

def peek_next_number(category_code):
    used = get_used_numbers(category_code)
    base = max([get_current_counter(category_code)] + (list(used) or [0]))
    n = base + 1
    while n in used:
        n += 1
    return str(n).zfill(3)

def reserve_next_number(category_code):
    used = get_used_numbers(category_code)
    base = max([get_current_counter(category_code)] + (list(used) or [0]))
    n = base + 1
    while n in used:
        n += 1
    set_counter(category_code, n)
    return str(n).zfill(3)

# ===================== OAuth-хранилище (Postgres) =====================

def save_oauth(access_token, refresh_token, expires_in, domain,
               member_id=None, bot_id=None, app_token=None):
    try:
        expires_at = int(time.time()) + int(expires_in or 3600) - 60
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO bitrix_oauth
                (id, access_token, refresh_token, expires_at, domain, member_id, bot_id, app_token)
            VALUES (1, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                access_token  = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at    = EXCLUDED.expires_at,
                domain        = EXCLUDED.domain,
                member_id     = COALESCE(EXCLUDED.member_id, bitrix_oauth.member_id),
                bot_id        = COALESCE(EXCLUDED.bot_id, bitrix_oauth.bot_id),
                app_token     = COALESCE(EXCLUDED.app_token, bitrix_oauth.app_token)
        """, (access_token, refresh_token, expires_at, domain, member_id, bot_id, app_token))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Ошибка save_oauth: {e}")

def load_oauth():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT access_token, refresh_token, expires_at, domain, member_id, bot_id, app_token
            FROM bitrix_oauth WHERE id=1
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        return {
            "access_token": row[0],
            "refresh_token": row[1],
            "expires_at": row[2] or 0,
            "domain": row[3],
            "member_id": row[4],
            "bot_id": row[5],
            "app_token": row[6],
        }
    except Exception as e:
        print(f"Ошибка load_oauth: {e}")
        return None

def set_bot_id(bot_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE bitrix_oauth SET bot_id=%s WHERE id=1", (str(bot_id),))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Ошибка set_bot_id: {e}")

def get_access_token():
    st = load_oauth()
    if not st:
        return None
    access = st.get("access_token")
    domain = st.get("domain")
    refresh = st.get("refresh_token")
    exp = st.get("expires_at") or 0
    if not access or not domain:
        return None
    if time.time() < exp:
        return access, domain
    if not refresh or not BITRIX_CLIENT_ID or not BITRIX_CLIENT_SECRET:
        print("[OAUTH] access_token истёк, refresh невозможен (нет refresh/CLIENT_ID/SECRET)")
        return None
    try:
        r = httpx.get(
            "https://oauth.bitrix.info/oauth/token/",
            params={
                "grant_type": "refresh_token",
                "client_id": BITRIX_CLIENT_ID,
                "client_secret": BITRIX_CLIENT_SECRET,
                "refresh_token": refresh,
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[OAUTH] refresh failed: {e}")
        return None
    na = data.get("access_token")
    nr = data.get("refresh_token") or refresh
    ein = int(data.get("expires_in") or 3600)
    nd = data.get("domain") or domain
    if not na:
        return None
    save_oauth(na, nr, ein, nd, st.get("member_id"))
    print("[OAUTH] access_token обновлён через refresh")
    return na, nd

# ===================== BITRIX REST =====================

def bx_call(method, params=None, auth=None):
    body = dict(params or {})
    if auth and auth.get("access_token") and auth.get("domain"):
        token, domain = auth["access_token"], auth["domain"]
    else:
        tok = get_access_token()
        if not tok:
            raise RuntimeError("Bitrix OAuth не настроен — приложение не установлено?")
        token, domain = tok
    if BITRIX_CLIENT_ID:
        body.setdefault("CLIENT_ID", BITRIX_CLIENT_ID)
    url = f"https://{domain}/rest/{method}?auth={token}"
    r = httpx.post(url, json=body, timeout=30)
    if r.status_code >= 400:
        try:
            b = r.json()
        except Exception:
            b = r.text
        raise RuntimeError(f"Bitrix {method} {r.status_code}: {b}")
    data = r.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"Bitrix {method} error: {data}")
    return data.get("result") if isinstance(data, dict) else data

def send_b24_message(dialog_id, text, auth=None):
    try:
        st = load_oauth() or {}
        params = {"DIALOG_ID": dialog_id, "MESSAGE": text}
        if st.get("bot_id"):
            params["BOT_ID"] = st["bot_id"]
        bx_call("imbot.message.add", params, auth=auth)
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def register_bot():
    if not EVENT_HANDLER_URL:
        raise RuntimeError("PUBLIC_BASE_URL не задан — некуда слать события")
    result = bx_call("imbot.register", {
        "CODE": BOT_CODE,
        "TYPE": "B",
        "EVENT_MESSAGE_ADD": EVENT_HANDLER_URL,
        "EVENT_WELCOME_MESSAGE": EVENT_HANDLER_URL,
        "EVENT_BOT_DELETE": EVENT_HANDLER_URL,
        "PROPERTIES": {
            "NAME": BOT_NAME,
            "COLOR": "AQUA",
            "EMAIL": "joto-bot@joto.local",
            "WORK_POSITION": "Бот для создания артикулов JOTO",
        },
    })
    bot_id = str(result)
    set_bot_id(bot_id)
    print(f"Бот зарегистрирован: BOT_ID={bot_id}")
    return bot_id

def register_left_menu():
    """Добавляет приложение отдельным пунктом в левое меню Bitrix24."""
    if not LEFT_MENU_HANDLER_URL:
        raise RuntimeError("PUBLIC_BASE_URL не задан — некуда вести пункт меню")
    # снимаем старую привязку (если была) — чтобы не плодить дубли
    try:
        bx_call("placement.unbind", {"PLACEMENT": "LEFT_MENU", "HANDLER": LEFT_MENU_HANDLER_URL})
    except Exception:
        pass
    result = bx_call("placement.bind", {
        "PLACEMENT": "LEFT_MENU",
        "HANDLER": LEFT_MENU_HANDLER_URL,
        "TITLE": LEFT_MENU_TITLE,
        "DESCRIPTION": "Массовое создание и редактирование карточек Wildberries",
    })
    print(f"Пункт левого меню зарегистрирован: {LEFT_MENU_HANDLER_URL}")
    return result

# ===================== ДИАЛОГ: СОЗДАНИЕ АРТИКУЛА =====================

def send_welcome(dialog_id, auth=None):
    user_states[dialog_id] = {"step": "start"}
    send_b24_message(dialog_id,
        f"👋 Привет! Я бот «{BOT_NAME}» для создания артикулов JOTO.\n\n"
        "Напиши *артикул* чтобы создать новый артикул.\n\n"
        f"Доступные категории:\n{CATS_LIST}",
        auth=auth,
    )

def handle_message(dialog_id, text, auth=None):
    text = text.strip()
    state = user_states.get(dialog_id, {})
    step = state.get("step", "start")

    print(f"handle_message: dialog_id={dialog_id}, step={step}, text={text}")

    if any(word in text.lower() for word in ["помощь", "help", "начать", "старт", "привет", "/start"]):
        send_welcome(dialog_id, auth=auth)
        return

    if text.lower() in ["артикул", "создать", "новый"]:
        user_states[dialog_id] = {"step": "wait_category"}
        send_b24_message(dialog_id,
            f"📦 *Создание артикула*\n\n"
            f"Шаг 1/3: Введите категорию товара:\n{CATS_LIST}",
            auth=auth,
        )
        return

    if step == "wait_category":
        category = text.lower()
        category_code = resolve_category_code(category)
        if not category_code:
            send_b24_message(dialog_id, f"❌ Категория не найдена.\n\nВведите одну из:\n{CATS_LIST}\n\n(новые категории можно добавить в приложении)", auth=auth)
            return
        next_num = peek_next_number(category_code)
        user_states[dialog_id] = {"step": "wait_color", "category": category, "category_code": category_code}
        send_b24_message(dialog_id,
            f"✅ Категория: {category.capitalize()} (J{category_code})\n"
            f"Следующий номер модели: *{next_num}*\n\n"
            f"Шаг 2/3: Введите цвет (например: black, white, grey, navy):",
            auth=auth,
        )
        return

    if step == "wait_color":
        color = text.lower().replace(" ", "")
        user_states[dialog_id]["color"] = color
        user_states[dialog_id]["step"] = "wait_name"
        send_b24_message(dialog_id, f"✅ Цвет: {color}\n\nШаг 3/3: Введите название товара:", auth=auth)
        return

    if step == "wait_name":
        category = state["category"]
        category_code = state["category_code"]
        color = state["color"]
        name = text
        model_number = reserve_next_number(category_code)
        article = f"J{category_code}{model_number}/{color}"
        user_states[dialog_id] = {"step": "start"}
        send_b24_message(dialog_id,
            f"✅ *Артикул создан!*\n\n"
            f"🏷 Артикул: *{article}*\n"
            f"📁 Категория: {category.capitalize()}\n"
            f"🎨 Цвет: {color}\n"
            f"📝 Название: {name}\n"
            f"🔢 Модель №{model_number}\n\n"
            f"Для создания ещё одного напиши *артикул*",
            auth=auth,
        )
        return

    send_b24_message(dialog_id, "Напиши *артикул* чтобы создать новый артикул, или *помощь* для справки.", auth=auth)

# ===================== CTR МОНИТОРИНГ =====================

previous_ctr = {}

def get_wb_ctr():
    try:
        today = datetime.now().date()
        date_from = (today - timedelta(days=2)).strftime("%Y-%m-%d")
        date_to = today.strftime("%Y-%m-%d")

        url = "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products"
        headers = {"Authorization": WB_API_TOKEN}
        payload = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "limit": 100,
            "offset": 0,
            "orderBy": {"field": "addToCartCount", "mode": "desc"},
            "selectedPeriod": {"begin": date_from, "end": date_to}
        }

        resp = httpx.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            print(f"WB API ошибка: {resp.text[:300]}")
            return {}

        data = resp.json()
        items = data.get("data", {}).get("products", []) or data.get("products", []) or []

        result = {}
        for item in items:
            nm_id = item.get("nmID") or item.get("nmId")
            name = item.get("vendorCode", str(nm_id))
            views = item.get("openCardCount", 0) or 0
            clicks = item.get("addToCartCount", 0) or 0
            if nm_id and views > 0:
                result[nm_id] = {"ctr": round(clicks / views * 100, 2), "name": name}

        print(f"CTR: получено артикулов {len(result)}")
        return result
    except Exception as e:
        print(f"Ошибка WB API: {e}")
        return {}

def check_ctr():
    global previous_ctr
    print(f"Проверка CTR: {datetime.now()}")
    current = get_wb_ctr()
    if not current:
        return

    alerts = []
    for nm_id, data in current.items():
        ctr = data["ctr"]
        name = data["name"]
        if nm_id in previous_ctr:
            prev_ctr = previous_ctr[nm_id]["ctr"]
            if prev_ctr > 0 and (prev_ctr - ctr) >= 1.0:
                alerts.append(f"⚠️ {name}: CTR снизился с {prev_ctr}% до {ctr}% (−{round(prev_ctr-ctr,2)}%)")

    previous_ctr = current

    if alerts:
        msg = "📉 *Снижение CTR на Wildberries:*\n\n" + "\n".join(alerts)
        send_b24_message(CTR_ALERT_DIALOG, msg)
    else:
        print("Снижений CTR не найдено")

# ===================== ОТЧЁТ ПО ЗАДАЧАМ =====================

def get_users():
    result = bx_call("user.get", {"ACTIVE": True, "USER_TYPE": "employee"})
    users = {}
    if isinstance(result, list):
        for u in result:
            uid = str(u.get("ID", ""))
            name = f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip()
            if uid and name:
                users[uid] = name
    return users

def get_tasks_for_user(user_id):
    today = datetime.now().strftime("%Y-%m-%dT00:00:00+03:00")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00+03:00")

    done = bx_call("tasks.task.list", {
        "filter": {"RESPONSIBLE_ID": user_id, "STATUS": 5, ">=CLOSED_DATE": today, "<CLOSED_DATE": tomorrow},
        "select": ["ID", "TITLE"]
    })
    overdue = bx_call("tasks.task.list", {
        "filter": {"RESPONSIBLE_ID": user_id, "!STATUS": [4, 5], "<=DEADLINE": today},
        "select": ["ID", "TITLE"]
    })
    in_progress = bx_call("tasks.task.list", {
        "filter": {"RESPONSIBLE_ID": user_id, "STATUS": 3},
        "select": ["ID", "TITLE"]
    })

    def extract_titles(res):
        if isinstance(res, dict):
            return [t.get("title", "") for t in res.get("tasks", [])]
        elif isinstance(res, list):
            return [t.get("title", t.get("TITLE", "")) for t in res]
        return []

    return extract_titles(done), extract_titles(in_progress), extract_titles(overdue)

def generate_report():
    print(f"Генерация отчёта: {datetime.now()}")
    users = get_users()
    if not users:
        send_b24_message(REPORT_USER_ID, "⚠️ Не удалось получить список сотрудников.")
        return

    today_str = datetime.now().strftime("%d.%m.%Y")
    report_lines = [f"📊 *Отчёт по задачам за {today_str}*"]

    for user_id, name in users.items():
        if user_id == REPORT_USER_ID:
            continue

        done, in_progress, overdue = get_tasks_for_user(user_id)
        lines = [f"\n👤 *{name}*"]

        if done:
            lines.append(f"✅ Выполнено ({len(done)}):")
            for t in done[:5]:
                lines.append(f"  • {t}")
            if len(done) > 5:
                lines.append(f"  ...и ещё {len(done)-5}")

        if in_progress:
            lines.append(f"🔄 В работе ({len(in_progress)}):")
            for t in in_progress[:5]:
                lines.append(f"  • {t}")
            if len(in_progress) > 5:
                lines.append(f"  ...и ещё {len(in_progress)-5}")

        if overdue:
            lines.append(f"❌ Просрочено ({len(overdue)}):")
            for t in overdue[:5]:
                lines.append(f"  • {t}")
            if len(overdue) > 5:
                lines.append(f"  ...и ещё {len(overdue)-5}")

        if not done and not in_progress and not overdue:
            lines.append("  — нет активных задач")

        report_lines.extend(lines)

    send_b24_message(REPORT_USER_ID, "\n".join(report_lines))
    print("Отчёт отправлен")

# ===================== FLASK: УСТАНОВКА И СОБЫТИЯ =====================

INSTALL_FINISH_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Установка</title>
<script src="//api.bitrix24.com/api/v1/"></script></head>
<body>
<p>Устанавливаю бота «Article Generator»...</p>
<script>
  BX24.init(function(){ BX24.installFinish(); });
</script>
</body></html>"""

@app.route("/install", methods=["GET", "POST"])
def install():
    vals = request.values
    auth_id = vals.get("AUTH_ID", "")
    refresh_id = vals.get("REFRESH_ID", "")
    expires = vals.get("AUTH_EXPIRES", "3600")
    member_id = vals.get("member_id", "")
    domain = vals.get("DOMAIN", "") or request.args.get("DOMAIN", "")
    app_token = vals.get("application_token", "") or vals.get("APP_SID", "")

    if auth_id and domain:
        init_db()
        save_oauth(auth_id, refresh_id, expires, domain, member_id, app_token=app_token or None)
        try:
            register_bot()
        except Exception as e:
            print(f"Ошибка регистрации бота при установке: {e}")
        try:
            register_left_menu()
        except Exception as e:
            print(f"Ошибка регистрации пункта левого меню при установке: {e}")
    else:
        print("[INSTALL] не пришли AUTH_ID/DOMAIN — проверь настройки приложения")

    return Response(INSTALL_FINISH_HTML, mimetype="text/html")

@app.route("/bitrix/events", methods=["POST"])
def bitrix_events():
    vals = request.values
    event = vals.get("event", "")
    app_token = vals.get("auth[application_token]", "")

    if BITRIX_APP_TOKEN and app_token and app_token != BITRIX_APP_TOKEN:
        return Response("forbidden", status=403)

    auth = {
        "access_token": vals.get("auth[access_token]", ""),
        "domain": vals.get("auth[domain]", ""),
    }
    if auth["access_token"] and auth["domain"]:
        prev = load_oauth() or {}
        save_oauth(
            auth["access_token"],
            vals.get("auth[refresh_token]", "") or prev.get("refresh_token"),
            vals.get("auth[expires_in]", "3600"),
            auth["domain"],
            vals.get("auth[member_id]", "") or prev.get("member_id"),
            app_token=app_token or None,
        )

    if event == "ONIMBOTMESSAGEADD":
        dialog_id = vals.get("data[PARAMS][DIALOG_ID]", "").strip()
        text = vals.get("data[PARAMS][MESSAGE]", "").strip()
        if dialog_id and text:
            threading.Thread(target=handle_message, args=(dialog_id, text, auth)).start()

    elif event in ("ONIMBOTWELCOMEMESSAGE", "ONIMBOTJOINCHAT"):
        dialog_id = vals.get("data[PARAMS][DIALOG_ID]", "").strip()
        if dialog_id:
            threading.Thread(target=send_welcome, args=(dialog_id, auth)).start()

    elif event in ("ONIMBOTDELETE", "ONAPPUNINSTALL"):
        print(f"Событие: {event}")

    return jsonify({"ok": True})

# ===================== FLASK: ИНТЕРФЕЙС ПРИЛОЖЕНИЯ =====================

APP_PAGE_HTML = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Генерация артикулов</title></head>
<body style="font-family:sans-serif;padding:24px"><h1>Генерация артикулов</h1>
<p>Файл интерфейса app_page.html не найден. Проверьте, что он рядом с main.py.</p></body></html>"""

APP_PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_page.html")

def load_app_page():
    try:
        with open(APP_PAGE_PATH, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Не удалось загрузить app_page.html: {e}")
        return APP_PAGE_HTML

def load_named_page(path, fallback="<!doctype html><meta charset='utf-8'><h1>Страница не найдена</h1>"):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Не удалось загрузить {path}: {e}")
        return fallback

LOGO_EXTS = ("png", "svg", "jpg", "jpeg", "webp")

def _logo_file():
    """Путь к файлу logo.* рядом с main.py, если загружен."""
    base = os.path.dirname(os.path.abspath(__file__))
    for ext in LOGO_EXTS:
        p = os.path.join(base, "logo." + ext)
        if os.path.exists(p):
            return p
    return None

@app.route("/logo", methods=["GET"])
def logo():
    p = _logo_file()
    if not p:
        return Response("not found", status=404)
    mt = mimetypes.guess_type(p)[0] or "application/octet-stream"
    with open(p, "rb") as f:
        return Response(f.read(), mimetype=mt)

@app.route("/", methods=["GET", "POST"])
def index():
    return Response(load_app_page(), mimetype="text/html")

@app.route("/api/config", methods=["GET"])
def api_config():
    logo_url = LOGO_URL or ("/logo" if _logo_file() else "")
    return jsonify({"ok": True, "logo_url": logo_url})

@app.route("/api/categories", methods=["GET"])
def api_categories():
    return jsonify({"ok": True, "categories": all_categories()})

@app.route("/api/category", methods=["POST"])
def api_add_category():
    data = request.get_json(silent=True) or request.form
    name = (data.get("name", "") or "").strip().lower()
    code = (data.get("code", "") or "").strip()
    ok, result = add_category(name, code or None)
    if not ok:
        return jsonify({"ok": False, "error": result}), 400
    return jsonify({"ok": True, "value": name, "title": name.capitalize(), "code": result})

@app.route("/api/next", methods=["GET"])
def api_next():
    category = (request.args.get("category", "") or "").strip().lower()
    code = resolve_category_code(category)
    if not code:
        return jsonify({"ok": False, "error": "Неизвестная категория"}), 400
    next_num = peek_next_number(code)
    return jsonify({"ok": True, "category_code": code, "next_number": next_num})

@app.route("/api/article", methods=["POST"])
def api_article():
    data = request.get_json(silent=True) or request.form
    category = (data.get("category", "") or "").strip().lower()
    color = (data.get("color", "") or "").strip().lower().replace(" ", "")
    name = (data.get("name", "") or "").strip()
    code = resolve_category_code(category)
    if not code:
        return jsonify({"ok": False, "error": "Неизвестная категория"}), 400
    if not color:
        return jsonify({"ok": False, "error": "Укажите цвет"}), 400
    model_number = reserve_next_number(code)
    article = f"J{code}{model_number}/{color}"
    return jsonify({
        "ok": True,
        "article": article,
        "category": category,
        "category_title": CATEGORY_TITLES.get(category, category.capitalize()),
        "category_code": code,
        "color": color,
        "name": name,
        "model_number": model_number,
    })

# ===================== WB CONTENT API: МАССОВЫЕ КАРТОЧКИ =====================
# Создание и редактирование карточек товаров на Wildberries.
# Требуется WB_API_TOKEN с доступом к категории «Контент».

WB_CONTENT_BASE = "https://content-api.wildberries.ru"
WB_CARDS_PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cards_page.html")

def wb_content_request(method, path, json_body=None, params=None, timeout=60):
    if not WB_API_TOKEN:
        raise RuntimeError("WB_API_TOKEN не задан — нужен токен с доступом к категории «Контент».")
    url = WB_CONTENT_BASE + path
    r = httpx.request(method, url, headers={"Authorization": WB_API_TOKEN},
                      json=json_body, params=params, timeout=timeout)
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:500]
        raise RuntimeError(f"WB {path} {r.status_code}: {detail}")
    try:
        return r.json()
    except Exception:
        return {}

def wb_fetch_cards(text_search="", limit=100, max_cards=1000):
    """Загружает карточки продавца (с пагинацией по курсору)."""
    cards = []
    page = max(1, min(int(limit or 100), 100))
    cursor = {"limit": page}
    for _ in range(200):
        flt = {"withPhoto": -1}
        if text_search:
            flt["textSearch"] = text_search
        payload = {"settings": {"cursor": cursor, "filter": flt}}
        data = wb_content_request("POST", "/content/v2/get/cards/list", json_body=payload)
        batch = data.get("cards", []) or []
        cards.extend(batch)
        cur = data.get("cursor", {}) or {}
        total = cur.get("total", 0)
        if total < cursor["limit"] or len(cards) >= max_cards:
            break
        cursor = {"limit": page, "updatedAt": cur.get("updatedAt"), "nmID": cur.get("nmID")}
    return cards[:max_cards]

def simplify_card(c):
    """Облегчённое представление карточки для интерфейса + сырой объект для обновления."""
    barcodes = []
    for s in (c.get("sizes") or []):
        for sku in (s.get("skus") or []):
            barcodes.append(sku)
    return {
        "nmID": c.get("nmID"),
        "imtID": c.get("imtID"),
        "vendorCode": c.get("vendorCode"),
        "brand": c.get("brand"),
        "title": c.get("title"),
        "description": c.get("description"),
        "subjectID": c.get("subjectID"),
        "subjectName": c.get("subjectName"),
        "barcodes": barcodes,
        "photos": len(c.get("photos") or []),
        "characteristics": [
            {"id": ch.get("id"), "name": ch.get("name"), "value": ch.get("value")}
            for ch in (c.get("characteristics") or [])
        ],
        "raw": c,
    }

def build_update_object(c):
    """Полный объект для /content/v2/cards/update — иначе WB сотрёт незаполненные поля."""
    return {
        "nmID": c.get("nmID"),
        "vendorCode": c.get("vendorCode"),
        "brand": c.get("brand"),
        "title": c.get("title"),
        "description": c.get("description"),
        "dimensions": c.get("dimensions") or {},
        "characteristics": c.get("characteristics") or [],
        "sizes": c.get("sizes") or [],
    }

def wb_update_cards(raw_cards):
    objs = [build_update_object(c) for c in raw_cards]
    results = []
    for i in range(0, len(objs), 1000):  # WB принимает до 3000 за запрос, шлём по 1000
        chunk = objs[i:i + 1000]
        results.append(wb_content_request("POST", "/content/v2/cards/update", json_body=chunk))
    return results

def wb_create_cards(items):
    return wb_content_request("POST", "/content/v2/cards/upload", json_body=items)

def wb_generate_barcodes(count):
    data = wb_content_request("POST", "/content/v2/barcodes", json_body={"count": int(count)})
    if isinstance(data, dict):
        d = data.get("data") or {}
        if isinstance(d, dict):
            return d.get("barcodes") or []
        if isinstance(d, list):
            return d
    return []

def wb_search_subjects(name="", limit=200):
    params = {"locale": "ru", "limit": limit}
    if name:
        params["name"] = name
    data = wb_content_request("GET", "/content/v2/object/all", params=params)
    return data.get("data", []) or []

def wb_subject_charcs(subject_id):
    data = wb_content_request("GET", f"/content/v2/object/charcs/{subject_id}", params={"locale": "ru"})
    return data.get("data", []) or []

def apply_bulk_field(raw, field, value, charc_id=None, charc_name=None):
    """Применяет одно изменение к сырой карточке (in-place)."""
    if field == "description":
        raw["description"] = value
    elif field == "title":
        raw["title"] = value
    elif field == "brand":
        raw["brand"] = value
    elif field == "vendorCode":
        raw["vendorCode"] = value
    elif field == "subjectID":
        try:
            raw["subjectID"] = int(value)
        except Exception:
            raw["subjectID"] = value
    elif field == "characteristic":
        chars = raw.get("characteristics") or []
        new_value = value if isinstance(value, list) else [value]
        found = False
        for ch in chars:
            if charc_id is not None and ch.get("id") == charc_id:
                ch["value"] = new_value
                found = True
                break
            if charc_name and ch.get("name") == charc_name:
                ch["value"] = new_value
                found = True
                break
        if not found and (charc_id is not None or charc_name):
            entry = {"value": new_value}
            if charc_id is not None:
                entry["id"] = charc_id
            if charc_name:
                entry["name"] = charc_name
            chars.append(entry)
        raw["characteristics"] = chars
    elif field == "barcode":
        sizes = raw.get("sizes") or []
        for s in sizes:
            skus = s.get("skus") or []
            if value and value not in skus:
                skus.append(value)
            s["skus"] = skus
        raw["sizes"] = sizes
    return raw

@app.route("/cards", methods=["GET", "POST"])
def cards_page():
    return Response(load_named_page(WB_CARDS_PAGE_PATH), mimetype="text/html")

@app.route("/api/wb/cards", methods=["GET"])
def api_wb_cards():
    search = (request.args.get("search", "") or "").strip()
    try:
        max_cards = int(request.args.get("limit", "1000"))
    except Exception:
        max_cards = 1000
    try:
        cards = wb_fetch_cards(text_search=search, limit=100, max_cards=max_cards)
        return jsonify({"ok": True, "count": len(cards),
                        "cards": [simplify_card(c) for c in cards]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

@app.route("/api/wb/bulk-edit", methods=["POST"])
def api_wb_bulk_edit():
    data = request.get_json(silent=True) or {}
    cards = data.get("cards") or []
    field = (data.get("field") or "").strip()
    value = data.get("value")
    charc_id = data.get("charcId")
    charc_name = data.get("charcName")
    if not cards:
        return jsonify({"ok": False, "error": "Не выбрано ни одной карточки"}), 400
    if not field:
        return jsonify({"ok": False, "error": "Не указано, что менять"}), 400
    try:
        for c in cards:
            apply_bulk_field(c, field, value, charc_id, charc_name)
        results = wb_update_cards(cards)
        errors = [r for r in results if isinstance(r, dict) and r.get("error")]
        if errors:
            msg = "; ".join(str(e.get("errorText") or e.get("additionalErrors") or "ошибка WB") for e in errors)
            return jsonify({"ok": False, "error": msg, "details": errors}), 502
        return jsonify({"ok": True, "updated": len(cards)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

@app.route("/api/wb/create", methods=["POST"])
def api_wb_create():
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not items:
        return jsonify({"ok": False, "error": "Нет карточек для создания"}), 400
    try:
        result = wb_create_cards(items)
        if isinstance(result, dict) and result.get("error"):
            msg = result.get("errorText") or result.get("additionalErrors") or "Ошибка WB"
            return jsonify({"ok": False, "error": str(msg), "details": result}), 502
        return jsonify({"ok": True, "created": len(items), "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

@app.route("/api/wb/barcodes", methods=["POST"])
def api_wb_barcodes():
    data = request.get_json(silent=True) or {}
    try:
        count = int(data.get("count", 1))
    except Exception:
        count = 1
    count = max(1, min(count, 5000))
    try:
        return jsonify({"ok": True, "barcodes": wb_generate_barcodes(count)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

@app.route("/api/wb/subjects", methods=["GET"])
def api_wb_subjects():
    name = (request.args.get("name", "") or "").strip()
    try:
        subjects = wb_search_subjects(name=name, limit=200)
        items = [{"subjectID": s.get("subjectID"), "subjectName": s.get("subjectName"),
                  "parentName": s.get("parentName")} for s in subjects]
        return jsonify({"ok": True, "subjects": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

@app.route("/api/wb/charcs", methods=["GET"])
def api_wb_charcs():
    try:
        subject_id = int(request.args.get("subjectId", "0"))
    except Exception:
        subject_id = 0
    if not subject_id:
        return jsonify({"ok": False, "error": "Не указан subjectId"}), 400
    try:
        charcs = wb_subject_charcs(subject_id)
        items = [{"id": c.get("charcID"), "name": c.get("name"), "required": c.get("required"),
                  "unitName": c.get("unitName"), "maxCount": c.get("maxCount"),
                  "charcType": c.get("charcType")} for c in charcs]
        return jsonify({"ok": True, "charcs": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

# ===================== НАЦИОНАЛЬНЫЙ КАТАЛОГ (ЧЕСТНЫЙ ЗНАК): ГТИНЫ =====================
# Каркас Варианта Б: создать товары в НК → получить ГТИНы → подставить в карточки WB.

@app.route("/api/nk/status", methods=["GET"])
def api_nk_status():
    return jsonify({"ok": True, **nk.nk_status()})

@app.route("/api/nk/create-gtins", methods=["POST"])
def api_nk_create_gtins():
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not items:
        return jsonify({"ok": False, "error": "Нет товаров для создания ГТИНов"}), 400
    try:
        gtin_map = nk.create_gtins_for_items(items)
        return jsonify({"ok": True, "gtins": gtin_map, "count": len(gtin_map)})
    except nk.NKNotConfigured as e:
        return jsonify({"ok": False, "error": str(e), "needs_config": True}), 400
    except nk.NKError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

# ===================== FLASK: СЕРВИСНЫЕ ЭНДПОИНТЫ =====================

@app.route("/admin/bitrix/register", methods=["GET"])
def admin_register():
    if not BITRIX_CLIENT_SECRET or request.args.get("secret", "") != BITRIX_CLIENT_SECRET:
        return Response("forbidden", status=403)
    try:
        bot_id = register_bot()
        return jsonify({"ok": True, "bot_id": bot_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/admin/bitrix/placement", methods=["GET"])
def admin_placement():
    if not BITRIX_CLIENT_SECRET or request.args.get("secret", "") != BITRIX_CLIENT_SECRET:
        return Response("forbidden", status=403)
    try:
        register_left_menu()
        return jsonify({"ok": True, "handler": LEFT_MENU_HANDLER_URL, "title": LEFT_MENU_TITLE})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/wb-refresh", methods=["GET"])
def api_wb_refresh():
    _wb_used_cache["ts"] = 0.0
    try:
        codes = fetch_wb_vendor_codes()
        _wb_used_cache["used"] = _used_numbers_by_code(codes)
        _wb_used_cache["ts"] = time.time()
        summary = {cc: len(s) for cc, s in _wb_used_cache["used"].items()}
        return jsonify({"ok": True, "total_codes": len(codes), "by_category": summary})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/check-now", methods=["GET"])
def check_now():
    threading.Thread(target=check_ctr).start()
    return jsonify({"ok": True})

@app.route("/report-now", methods=["GET"])
def report_now():
    threading.Thread(target=generate_report).start()
    return jsonify({"ok": True, "message": "Отчёт генерируется"})

# ===================== ЗАПУСК =====================

def run_scheduler():
    schedule.every().day.at("06:00").do(check_ctr)        # 09:00 МСК
    schedule.every().day.at("15:00").do(generate_report)  # 18:00 МСК
    print("Планировщик запущен:")
    print("  - CTR проверка каждый день в 09:00 МСК")
    print("  - Отчёт по задачам каждый день в 18:00 МСК")
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=run_scheduler, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
