import os
import time
import threading
import schedule
import httpx
import psycopg2
from flask import Flask, request, jsonify, Response
from datetime import datetime, timedelta

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

# Имя бота, которое увидят пользователи в Битриксе
BOT_NAME = "Article Generator"
BOT_CODE = "joto_article_bot"

EVENT_HANDLER_URL = f"{PUBLIC_BASE_URL}/bitrix/events" if PUBLIC_BASE_URL else ""

# Категории по инструкции JOTO
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
        conn.commit()
        cur.close()
        conn.close()
        print("БД инициализирована")
    except Exception as e:
        print(f"Ошибка БД: {e}")

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

# ===================== OAuth-хранилище (Postgres) =====================

def save_oauth(access_token, refresh_token, expires_in, domain,
               member_id=None, bot_id=None, app_token=None):
    """Сохранить/обновить OAuth-токены приложения. Храним одну строку id=1."""
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
    """Вернуть (access_token, domain). Если истёк — обновить через refresh_token."""
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
    # истёк — рефрешим
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
    """Вызов Bitrix REST. auth (из события) имеет приоритет, иначе берём токен из БД."""
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
    """Зарегистрировать бота в Битриксе (imbot.register). Возвращает BOT_ID."""
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
        if category not in CATEGORIES:
            send_b24_message(dialog_id, f"❌ Категория не найдена.\n\nВведите одну из:\n{CATS_LIST}", auth=auth)
            return
        category_code = CATEGORIES[category]
        current = get_current_counter(category_code)
        next_num = str(current + 1).zfill(3)
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
        model_number = get_next_model_number(category_code)
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
    """Обработчик установки локального приложения Битрикс24 (ONAPPINSTALL)."""
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
    else:
        print("[INSTALL] не пришли AUTH_ID/DOMAIN — проверь настройки приложения")

    return Response(INSTALL_FINISH_HTML, mimetype="text/html")

@app.route("/bitrix/events", methods=["POST"])
def bitrix_events():
    """Приём событий бота: ONIMBOTMESSAGEADD, приветствие, удаление."""
    vals = request.values
    event = vals.get("event", "")
    app_token = vals.get("auth[application_token]", "")

    # Проверка подписи (если задан BITRIX_APP_TOKEN)
    if BITRIX_APP_TOKEN and app_token and app_token != BITRIX_APP_TOKEN:
        return Response("forbidden", status=403)

    # Свежий токен и домен прямо из события — используем сразу и обновляем кэш
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

APP_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Генерация артикулов</title>
<script src="//api.bitrix24.com/api/v1/"></script>
<style>
  *{box-sizing:border-box}
  body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:28px;color:#1a1a2e;background:#f5f7fb}
  .card{max-width:520px;margin:0 auto;background:#fff;border-radius:14px;padding:26px 30px;box-shadow:0 2px 12px rgba(0,0,0,.06)}
  h1{font-size:21px;margin:0 0 18px}
  label{display:block;font-size:13px;color:#5a5a72;margin:14px 0 6px}
  select,input{width:100%;padding:11px 12px;font-size:15px;border:1px solid #d7dbe6;border-radius:9px;background:#fff}
  select:focus,input:focus{outline:none;border-color:#3b82f6}
  .hint{font-size:12px;color:#8a8aa0;margin-top:6px}
  button{margin-top:20px;width:100%;padding:13px;font-size:15px;font-weight:600;color:#fff;background:#2563eb;border:none;border-radius:9px;cursor:pointer}
  button:hover{background:#1d4ed8}
  button:disabled{background:#9db8ee;cursor:default}
  .result{margin-top:20px;padding:18px;border-radius:11px;background:#ecfdf3;border:1px solid #b6ebc9;display:none}
  .result.show{display:block}
  .article{font-size:24px;font-weight:700;color:#0c7a44;letter-spacing:.5px}
  .meta{font-size:13px;color:#3f6f52;margin-top:6px;line-height:1.6}
  .copy{margin-top:12px;width:auto;padding:8px 16px;font-size:13px;background:#0c7a44}
  .copy:hover{background:#0a6236}
  .err{margin-top:16px;color:#c0392b;font-size:14px;display:none}
  .err.show{display:block}
</style></head>
<body>
<div class="card">
  <h1>🏷 Генерация артикулов</h1>

  <label>Категория товара</label>
  <select id="category">
    <option value="">— выберите категорию —</option>
    <option value="жилет">Жилет (01)</option>
    <option value="куртка">Куртка (02)</option>
    <option value="водолазка">Водолазка (03)</option>
    <option value="джинсы">Джинсы (04)</option>
    <option value="худи">Худи (05)</option>
    <option value="свитер">Свитер (06)</option>
    <option value="лонгслив">Лонгслив (07)</option>
    <option value="брюки">Брюки (09)</option>
    <option value="шорты">Шорты (10)</option>
    <option value="футболка">Футболка (11)</option>
  </select>
  <div class="hint" id="nextHint"></div>

  <label>Цвет (латиницей)</label>
  <input id="color" placeholder="например: black, white, grey, navy" autocomplete="off">

  <label>Название товара</label>
  <input id="name" placeholder="например: Oversize Hoodie" autocomplete="off">

  <button id="go">Создать артикул</button>

  <div class="err" id="err"></div>

  <div class="result" id="result">
    <div class="article" id="article"></div>
    <div class="meta" id="meta"></div>
    <button class="copy" id="copy">Скопировать артикул</button>
  </div>
</div>

<script>
try{ BX24.init(function(){ BX24.fitWindow && BX24.fitWindow(); }); }catch(e){}

var cat=document.getElementById('category'), colorEl=document.getElementById('color'),
    nameEl=document.getElementById('name'), go=document.getElementById('go'),
    err=document.getElementById('err'), res=document.getElementById('result'),
    artEl=document.getElementById('article'), metaEl=document.getElementById('meta'),
    hint=document.getElementById('nextHint'), copyBtn=document.getElementById('copy');

cat.addEventListener('change', function(){
  hint.textContent='';
  if(!cat.value) return;
  fetch('/api/next?category='+encodeURIComponent(cat.value))
    .then(function(r){return r.json()})
    .then(function(d){ if(d.ok) hint.textContent='Следующий номер модели: '+d.next_number; })
    .catch(function(){});
});

function showErr(m){ err.textContent=m; err.classList.add('show'); res.classList.remove('show'); }

go.addEventListener('click', function(){
  err.classList.remove('show'); res.classList.remove('show');
  if(!cat.value){ showErr('Выберите категорию'); return; }
  if(!colorEl.value.trim()){ showErr('Укажите цвет'); return; }
  go.disabled=true; go.textContent='Создаю...';
  fetch('/api/article', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({category:cat.value, color:colorEl.value, name:nameEl.value})
  }).then(function(r){return r.json()}).then(function(d){
    go.disabled=false; go.textContent='Создать артикул';
    if(!d.ok){ showErr(d.error||'Ошибка'); return; }
    artEl.textContent=d.article;
    metaEl.innerHTML='Категория: '+d.category_title+' &nbsp;•&nbsp; Цвет: '+d.color+
      ' &nbsp;•&nbsp; Модель №'+d.model_number+(d.name?(' &nbsp;•&nbsp; '+d.name):'');
    res.classList.add('show');
    hint.textContent='';
  }).catch(function(){
    go.disabled=false; go.textContent='Создать артикул';
    showErr('Не удалось связаться с сервером');
  });
});

copyBtn.addEventListener('click', function(){
  var t=artEl.textContent;
  if(navigator.clipboard){ navigator.clipboard.writeText(t); }
  copyBtn.textContent='Скопировано ✓';
  setTimeout(function(){ copyBtn.textContent='Скопировать артикул'; }, 1500);
});
</script>
</body></html>"""

# Человекочитаемые названия категорий для интерфейса
CATEGORY_TITLES = {
    "жилет": "Жилет", "куртка": "Куртка", "водолазка": "Водолазка",
    "джинсы": "Джинсы", "худи": "Худи", "свитер": "Свитер",
    "лонгслив": "Лонгслив", "брюки": "Брюки", "шорты": "Шорты", "футболка": "Футболка",
}

@app.route("/", methods=["GET", "POST"])
def index():
    return Response(APP_PAGE_HTML, mimetype="text/html")

@app.route("/api/next", methods=["GET"])
def api_next():
    """Показать следующий номер модели для категории БЕЗ увеличения счётчика."""
    category = (request.args.get("category", "") or "").strip().lower()
    if category not in CATEGORIES:
        return jsonify({"ok": False, "error": "Неизвестная категория"}), 400
    code = CATEGORIES[category]
    next_num = str(get_current_counter(code) + 1).zfill(3)
    return jsonify({"ok": True, "category_code": code, "next_number": next_num})

@app.route("/api/article", methods=["POST"])
def api_article():
    """Создать артикул: увеличить счётчик и вернуть готовый артикул."""
    data = request.get_json(silent=True) or request.form
    category = (data.get("category", "") or "").strip().lower()
    color = (data.get("color", "") or "").strip().lower().replace(" ", "")
    name = (data.get("name", "") or "").strip()
    if category not in CATEGORIES:
        return jsonify({"ok": False, "error": "Неизвестная категория"}), 400
    if not color:
        return jsonify({"ok": False, "error": "Укажите цвет"}), 400
    code = CATEGORIES[category]
    model_number = get_next_model_number(code)
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

# ===================== FLASK: СЕРВИСНЫЕ ЭНДПОИНТЫ =====================

@app.route("/admin/bitrix/register", methods=["GET"])
def admin_register():
    """Ручная (пере)регистрация бота. Защита: ?secret=BITRIX_CLIENT_SECRET."""
    if not BITRIX_CLIENT_SECRET or request.args.get("secret", "") != BITRIX_CLIENT_SECRET:
        return Response("forbidden", status=403)
    try:
        bot_id = register_bot()
        return jsonify({"ok": True, "bot_id": bot_id})
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
