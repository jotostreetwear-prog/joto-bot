import os
import json
import httpx
import threading
import schedule
import time
import psycopg2
from flask import Flask, request, jsonify
from datetime import datetime, timedelta

app = Flask(__name__)

WB_API_TOKEN = os.environ.get("WB_API_TOKEN", "").strip()
B24_WEBHOOK = os.environ.get("B24_WEBHOOK", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# Категории артикулов
CATEGORIES = {
    "жилет": "01",
    "толстовка": "02",
    "свитшот": "03",
    "худи": "05",
    "футболка": "06",
    "лонгслив": "07",
    "шорты": "08",
    "штаны": "09",
    "куртка": "10",
    "пальто": "11",
    "платье": "12",
    "юбка": "13",
    "кардиган": "14",
    "рубашка": "15",
    "пиджак": "16",
}

# Состояния диалога
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
            CREATE TABLE IF NOT EXISTS ctr_history (
                nm_id BIGINT,
                date DATE,
                ctr FLOAT,
                PRIMARY KEY (nm_id, date)
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

# ===================== БИТРИКС =====================

def send_b24_message(dialog_id, text):
    try:
        url = f"{B24_WEBHOOK}/im.message.add.json"
        resp = httpx.post(url, json={"DIALOG_ID": dialog_id, "MESSAGE": text}, timeout=10)
        print(f"Ответ Битрикс: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"Ошибка отправки: {e}")

# ===================== АРТИКУЛЫ =====================

def generate_article(category_code, model_number, color):
    color_clean = color.lower().replace(" ", "").replace("-", "")
    return f"J{category_code}{model_number}/{color_clean}"

def handle_message(user_id, text):
    text = text.strip()
    state = user_states.get(user_id, {})
    step = state.get("step", "start")

    if any(word in text.lower() for word in ["помощь", "help", "начать", "старт", "привет"]):
        user_states[user_id] = {"step": "start"}
        send_b24_message(user_id,
            "👋 Привет! Я бот JOTO.\n\n"
            "Я умею:\n"
            "• Создавать артикулы — напиши *артикул*\n"
            "• Показывать категории — напиши *категории*"
        )
        return

    if text.lower() == "категории":
        cats = "\n".join([f"• {k} ({v})" for k, v in CATEGORIES.items()])
        send_b24_message(user_id, f"📋 Доступные категории:\n{cats}")
        return

    if text.lower() in ["артикул", "создать", "новый"]:
        user_states[user_id] = {"step": "wait_category"}
        cats = ", ".join(CATEGORIES.keys())
        send_b24_message(user_id, f"Введите категорию товара:\n{cats}")
        return

    if step == "wait_category":
        category = text.lower()
        if category not in CATEGORIES:
            cats = ", ".join(CATEGORIES.keys())
            send_b24_message(user_id, f"❌ Категория не найдена. Выберите из списка:\n{cats}")
            return
        user_states[user_id] = {"step": "wait_color", "category": category}
        send_b24_message(user_id, "Введите цвет (например: black, white, navy):")
        return

    if step == "wait_color":
        user_states[user_id]["color"] = text
        user_states[user_id]["step"] = "wait_name"
        send_b24_message(user_id, "Введите название товара:")
        return

    if step == "wait_name":
        category = state["category"]
        color = state["color"]
        name = text

        category_code = CATEGORIES[category]
        model_number = get_next_model_number(category_code)
        article = generate_article(category_code, model_number, color)

        user_states[user_id] = {"step": "start"}
        send_b24_message(user_id,
            f"✅ Артикул создан!\n\n"
            f"📦 Артикул: *{article}*\n"
            f"📁 Категория: {category}\n"
            f"🎨 Цвет: {color}\n"
            f"📝 Название: {name}\n"
            f"🔢 Модель №{model_number}"
        )
        return

    # Если не попали ни в одно условие
    send_b24_message(user_id, "Напишите *артикул* чтобы создать новый артикул, или *помощь* для справки.")

# ===================== CTR МОНИТОРИНГ =====================

def get_wb_ctr():
    try:
        today = datetime.now().date()
        date_from = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        date_to = today.strftime("%Y-%m-%d")

        url = "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products"
        headers = {"Authorization": WB_API_TOKEN}
        payload = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "limit": 100,
            "offset": 0,
            "orderBy": {"field": "addToCartCount", "mode": "desc"},
            "selectedPeriod": {
                "begin": date_from,
                "end": date_to
            }
        }

        resp = httpx.post(url, headers=headers, json=payload, timeout=30)
        print(f"WB API статус: {resp.status_code}")
        if resp.status_code != 200:
            print(f"WB API ошибка: {resp.text[:300]}")
            return {}

        data = resp.json()
        items = data.get("data", {}).get("products", []) or data.get("products", []) or []

        result = {}
        for item in items:
            nm_id = item.get("nmID") or item.get("nmId")
            views = item.get("openCardCount", 0) or 0
            clicks = item.get("addToCartCount", 0) or 0
            if nm_id and views > 0:
                result[nm_id] = round(clicks / views * 100, 2)

        print(f"Получено артикулов: {len(result)}")
        return result

    except Exception as e:
        print(f"Ошибка WB API: {e}")
        return {}

def check_ctr():
    print(f"Проверка CTR: {datetime.now()}")
    if not WB_API_TOKEN or not B24_WEBHOOK:
        print("Нет токенов")
        return

    current = get_wb_ctr()
    if not current:
        print("Нет данных CTR")
        return

    alerts = []
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    try:
        conn = get_db()
        cur = conn.cursor()

        for nm_id, ctr in current.items():
            # Получаем вчерашний CTR
            cur.execute("SELECT ctr FROM ctr_history WHERE nm_id=%s AND date=%s", (nm_id, yesterday))
            row = cur.fetchone()
            if row:
                prev_ctr = row[0]
                if prev_ctr > 0 and ctr < prev_ctr and (prev_ctr - ctr) >= 1.0:
                    alerts.append(f"⚠️ Артикул {nm_id}: CTR снизился с {prev_ctr}% до {ctr}% (−{round(prev_ctr-ctr,2)}%)")

            # Сохраняем текущий CTR
            cur.execute("""
                INSERT INTO ctr_history (nm_id, date, ctr)
                VALUES (%s, %s, %s)
                ON CONFLICT (nm_id, date) DO UPDATE SET ctr = EXCLUDED.ctr
            """, (nm_id, today, ctr))

        conn.commit()
        cur.close()
        conn.close()

        if alerts:
            msg = "📉 *Снижение CTR на Wildberries:*\n\n" + "\n".join(alerts)
            send_b24_message("chat2024", msg)
            print(f"Отправлено {len(alerts)} уведомлений")
        else:
            print("Снижений CTR не обнаружено")

    except Exception as e:
        print(f"Ошибка проверки CTR: {e}")

# ===================== FLASK =====================

@app.route("/", methods=["GET"])
def index():
    return "JOTO Bot работает ✓"

@app.route("/check-now", methods=["GET"])
def check_now():
    threading.Thread(target=check_ctr).start()
    return jsonify({"ok": True, "message": "CTR проверка запущена"})

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        print("=== INCOMING REQUEST ===")
        print("Content-Type:", request.content_type)

        if request.content_type and "application/json" in request.content_type:
            data = request.json or {}
        else:
            data = request.form.to_dict()

        print("Form data:", data)

        from_user_id = data.get("data[PARAMS][FROM_USER_ID]", "").strip()
        dialog_id = data.get("data[PARAMS][DIALOG_ID]", "").strip()
        text = data.get("data[PARAMS][MESSAGE]", "").strip()

        user_id = from_user_id or dialog_id
        print(f"user_id={user_id}, text={text}")

        if user_id and text:
            threading.Thread(target=handle_message, args=(user_id, text)).start()

        return jsonify({"ok": True})

    except Exception as e:
        print(f"Ошибка webhook: {e}")
        return jsonify({"ok": False, "error": str(e)})

# ===================== ЗАПУСК =====================

def run_scheduler():
    schedule.every().day.at("06:00").do(check_ctr)
    print("Планировщик запущен — проверка каждый день в 09:00 МСК")
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=run_scheduler, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
