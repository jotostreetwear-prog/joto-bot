import os
import json
import httpx
from flask import Flask, request, jsonify

app = Flask(__name__)

# === НАСТРОЙКИ — вставь свои ключи ===
WB_API_TOKEN = os.environ.get("WB_API_TOKEN", "ВСТАВЬ_ТОКЕН_WB_СЮДА")
B24_WEBHOOK = os.environ.get("B24_WEBHOOK", "ВСТАВЬ_ВЕБХУК_БИТРИКСА_СЮДА")

# Категории JOTO
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
    "01": "Жилеты",
    "02": "Куртки",
    "03": "Водолазки",
    "04": "Джинсы",
    "05": "Худи",
    "06": "Свитеры",
    "07": "Лонгсливы",
    "09": "Брюки",
    "10": "Шорты",
    "11": "Футболки",
}


def generate_articul(category_code: str, model_num: int, color: str) -> str:
    """Генерирует артикул по правилам JOTO: J + код + номер модели + /цвет"""
    return f"J{category_code}{str(model_num).zfill(3)}/{color.lower().strip()}"


def create_wb_card(articul: str, title: str, category_code: str, color: str) -> dict:
    """Создаёт карточку товара на Wildberries"""
    wb_category = WB_CATEGORIES.get(category_code, "Одежда")

    payload = {
        "subjectName": wb_category,
        "variants": [
            {
                "vendorCode": articul,
                "title": title,
                "description": f"{title}. Артикул: {articul}",
                "brand": "Joto",
                "dimensions": {
                    "length": 30,
                    "width": 20,
                    "height": 5
                },
                "characteristics": [
                    {"Цвет": [color]},
                    {"Бренд": ["Joto"]},
                ]
            }
        ]
    }

    headers = {
        "Authorization": WB_API_TOKEN,
        "Content-Type": "application/json"
    }

    response = httpx.post(
        "https://content-api.wildberries.ru/content/v2/cards/upload",
        json=[payload],
        headers=headers,
        timeout=30
    )

    return response.json()


def send_b24_message(user_id: str, text: str):
    """Отправляет сообщение пользователю в Битрикс24"""
    httpx.post(
        f"{B24_WEBHOOK}/im.message.add.json",
        json={"DIALOG_ID": user_id, "MESSAGE": text},
        timeout=10
    )


def parse_message(text: str) -> dict | None:
    """
    Парсит сообщение от пользователя.
    Ожидаемый формат:
    категория: худи
    модель: 3
    цвет: black
    название: Худи оверсайз мужское
    """
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

    try:
        model_num = int(data.get("модель", "0"))
    except ValueError:
        return None

    color = data.get("цвет", "")
    title = data.get("название", "")

    if not all([category_code, model_num, color, title]):
        return None

    return {
        "category_code": category_code,
        "model_num": model_num,
        "color": color,
        "title": title
    }


@app.route("/", methods=["GET"])
def index():
    return "JOTO Bot работает ✓"


@app.route("/webhook", methods=["POST"])
def webhook():
    """Обработчик сообщений от Битрикс24"""
    try:
        data = request.json or request.form.to_dict()
        user_id = data.get("data[USER][ID]") or data.get("USER_ID", "")
        text = data.get("data[MESSAGE]") or data.get("MESSAGE", "")

        if not text:
            return jsonify({"ok": True})

        text = text.strip()

        # Помощь
        if text.lower() in ["помощь", "help", "/help", "start", "/start"]:
            send_b24_message(user_id,
                "👋 Привет! Я создаю артикулы и карточки товаров на Wildberries.\n\n"
                "Напиши мне в таком формате:\n\n"
                "категория: худи\n"
                "модель: 3\n"
                "цвет: black\n"
                "название: Худи оверсайз мужское\n\n"
                "Доступные категории:\n"
                "жилет, куртка, водолазка, джинсы, худи,\n"
                "свитер, лонгслив, брюки, шорты, футболка"
            )
            return jsonify({"ok": True})

        # Парсим сообщение
        parsed = parse_message(text)

        if not parsed:
            send_b24_message(user_id,
                "❌ Не понял формат. Напиши 'помощь' чтобы увидеть пример."
            )
            return jsonify({"ok": True})

        # Генерируем артикул
        articul = generate_articul(
            parsed["category_code"],
            parsed["model_num"],
            parsed["color"]
        )

        send_b24_message(user_id, f"⏳ Создаю карточку товара...\nАртикул: {articul}")

        # Создаём карточку на WB
        result = create_wb_card(
            articul,
            parsed["title"],
            parsed["category_code"],
            parsed["color"]
        )

        # Проверяем результат
        if result.get("error"):
            send_b24_message(user_id,
                f"❌ Ошибка WB: {result.get('errorText', 'неизвестная ошибка')}\n"
                f"Артикул {articul} сгенерирован, но карточка не создана."
            )
        else:
            send_b24_message(user_id,
                f"✅ Готово!\n\n"
                f"Артикул: {articul}\n"
                f"Название: {parsed['title']}\n"
                f"Карточка создана на Wildberries!"
            )

    except Exception as e:
        print(f"Ошибка: {e}")

    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
