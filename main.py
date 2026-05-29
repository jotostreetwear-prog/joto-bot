import os
import httpx
from flask import Flask, request, jsonify

app = Flask(__name__)

WB_API_TOKEN = os.environ.get("WB_API_TOKEN", "")
B24_WEBHOOK = os.environ.get("B24_WEBHOOK", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

CATEGORIES = {
    "жилет": "01", "жилеты": "01",
    "куртка": "02", "куртки": "02",
    "водолазка": "03", "водолазки": "03",
    "джинсы": "04", "джинс": "04",
    "худи": "05",
    "свитер": "06", "свитера": "06",
    "лонгслив": "07", "лонгсливы": "07",
    "брюки": "09", "брюк": "09",
    "шорты": "10", "шорт": "10",
    "футболка": "11", "футболки": "11",
}

WB_CATEGORIES = {
    "01": "Жилеты", "02": "Куртки", "03": "Водолазки",
    "04": "Джинсы", "05": "Худи", "06": "Свитеры",
    "07": "Лонгсливы", "09": "Брюки", "10": "Шорты", "11": "Футболки",
}

user_states = {}


def generate_articul(category_code: str, model_num: int, color: str) -> str:
    return f"J{category_code}{str(model_num).zfill(3)}/{color.lower().strip()}"


def generate_description(title: str, category: str, color: str) -> str:
    prompt = (
        f"Напиши короткое продающее описание товара для карточки на Wildberries.\n"
        f"Товар: {title}\nКатегория: {category}\nЦвет: {color}\n"
        f"Требования: 2-3 предложения, на русском, упомяни бренд Joto. Только текст, без заголовков."
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    resp = httpx.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def create_wb_card(articul: str, title: str, description: str, category_code: str, color: str) -> dict:
    wb_category = WB_CATEGORIES.get(category_code, "Одежда")
    payload = {
        "subjectName": wb_category,
        "variants": [{
            "vendorCode": articul,
            "title": title,
            "description": description,
            "brand": "Joto",
            "dimensions": {"length": 30, "width": 20, "height": 5},
            "characteristics": [{"Цвет": [color]}, {"Бренд": ["Joto"]}]
        }]
    }
    headers = {"Authorization": WB_API_TOKEN, "Content-Type": "application/json"}
    response = httpx.post(
        "https://content-api.wildberries.ru/content/v2/cards/upload",
        json=[payload], headers=headers, timeout=30
    )
    return response.json()


def send_b24_message(dialog_id: str, text: str):
    print(f"Отправляю: DIALOG_ID={dialog_id}")
    try:
        resp = httpx.post(
            f"{B24_WEBHOOK}/im.message.add.json",
            json={"DIALOG_ID": dialog_id, "MESSAGE": text},
            timeout=10
        )
        print(f"Ответ Битрикс: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Ошибка отправки: {e}")


def parse_first_message(text: str) -> dict | None:
    lines = text.lower().strip().split("\n")
    data = {}
    for line in lines:
        if ":" in line:
            key, _, val = line.partition(":")
            data[key.strip()] = val.strip()

    category_word = data.get("категория", "")
    category_code = CATEGORIES.get(category_word)
    if not category_code:
        return None

    color = data.get("цвет", "")
    title = data.get("название", "")
    if not color or not title:
        return None

    return {"category_code": category_code, "color": color, "title": title}


@app.route("/", methods=["GET"])
def index():
    return "JOTO Bot работает ✓"


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if request.content_type and 'application/json' in request.content_type:
            data = request.json or {}
        else:
            data = request.form.to_dict()

        dialog_id = data.get("data[PARAMS][DIALOG_ID]", "").strip()
        from_user_id = data.get("data[PARAMS][FROM_USER_ID]", "").strip()
        text = data.get("data[PARAMS][MESSAGE]", "").strip()

        print(f"dialog_id={dialog_id}, from_user_id={from_user_id}, text={text}")

        if not text or not dialog_id:
            return jsonify({"ok": True})

        state_key = from_user_id or dialog_id

        if text.lower() in ["помощь", "help", "/help", "start", "/start"]:
            send_b24_message(dialog_id,
                "👋 Привет! Я создаю артикулы и карточки товаров на Wildberries.\n\n"
                "Напиши мне в таком формате:\n\n"
                "категория: худи\n"
                "цвет: black\n"
                "название: Худи оверсайз мужское\n\n"
                "Доступные категории:\n"
                "жилет, куртка, водолазка, джинсы, худи,\n"
                "свитер, лонгслив, брюки, шорты, футболка"
            )
            return jsonify({"ok": True})

        if state_key in user_states:
            state = user_states[state_key]
            try:
                model_num = int(text)
                if model_num < 1:
                    raise ValueError
            except ValueError:
                send_b24_message(dialog_id, "❌ Введи просто число, например: 3")
                return jsonify({"ok": True})

            category_code = state["category_code"]
            color = state["color"]
            title = state["title"]

            articul = generate_articul(category_code, model_num, color)
            send_b24_message(dialog_id, f"⏳ Генерирую описание и создаю карточку...\nАртикул: {articul}")

            description = generate_description(title, WB_CATEGORIES.get(category_code, ""), color)
            result = create_wb_card(articul, title, description, category_code, color)

            del user_states[state_key]

            if result.get("error"):
                send_b24_message(dialog_id,
                    f"❌ Ошибка WB: {result.get('errorText', 'неизвестная ошибка')}\n"
                    f"Артикул {articul} сгенерирован, но карточка не создана."
                )
            else:
                send_b24_message(dialog_id,
                    f"✅ Готово!\n\n"
                    f"Артикул: {articul}\n"
                    f"Название: {title}\n\n"
                    f"Описание:\n{description}\n\n"
                    f"Карточка создана на Wildberries!"
                )
            return jsonify({"ok": True})

        parsed = parse_first_message(text)
        if not parsed:
            send_b24_message(dialog_id,
                "❌ Не понял формат. Напиши 'помощь' чтобы увидеть пример."
            )
            return jsonify({"ok": True})

        user_states[state_key] = parsed
        send_b24_message(dialog_id,
            f"📦 Категория: {WB_CATEGORIES.get(parsed['category_code'])}\n"
            f"Цвет: {parsed['color']}\n"
            f"Название: {parsed['title']}\n\n"
            f"Это какая модель по счёту? (введи число, например: 3)"
        )

    except Exception as e:
        print(f"Ошибка: {e}")

    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
