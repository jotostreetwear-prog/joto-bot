"""
Интеграция с Национальным каталогом (Честный ЗНАК) — КАРКАС (Вариант Б).

Назначение сценария:
    1. Массово создать карточки товаров в Национальном каталоге (НК).
    2. Получить на них присвоенные ГТИНы.
    3. Передать ГТИНы в режим «Создать массово» — они подставляются как
       баркоды (sizes[].skus) при создании карточек на Wildberries.

⚠️  Это КАРКАС. HTTP-слой и структура готовы, но точные пути эндпоинтов и
    формат полей товара НУЖНО СВЕРИТЬ с документацией в личном кабинете
    «Честный ЗНАК»:  товарная группа → «Помощь» → «API» → «API Национального
    каталога».  Все места, требующие сверки, помечены  TODO[NK-DOCS].

Переменные окружения:
    NK_API_KEY   — apikey Национального каталога (постоянный ключ из ЛК)
    NK_PARTY_ID  — идентификатор владельца товара (party_id / suppliers_id)
    NK_BASE_URL  — базовый URL API НК (по умолчанию ниже; сверьте с документацией)
"""

import os
import time
import httpx

# ===================== КОНФИГ =====================

NK_API_KEY = os.environ.get("NK_API_KEY", "").strip()
NK_PARTY_ID = os.environ.get("NK_PARTY_ID", "").strip()
# TODO[NK-DOCS]: подставьте базовый URL из документации НК (ЛК «Честный ЗНАК»).
NK_BASE_URL = os.environ.get("NK_BASE_URL", "https://api.national-catalog.ru").strip().rstrip("/")

# TODO[NK-DOCS]: сверьте пути эндпоинтов с документацией «API Национального каталога».
NK_PATHS = {
    "feed_info": "/v3/feed-info",        # информация об аккаунте/правах
    "categories": "/v3/categories",      # дерево категорий НК
    "attributes": "/v3/attributes",      # атрибуты категории
    "product_create": "/v3/product-create",  # создание товара (черновика)
    "product_list": "/v3/product-list",  # список товаров (с присвоенными ГТИН)
}

# Сколько раз и с каким интервалом опрашивать НК в ожидании присвоения ГТИН.
GTIN_POLL_ATTEMPTS = 10
GTIN_POLL_INTERVAL = 3  # секунд


# ===================== ИСКЛЮЧЕНИЯ =====================

class NKError(RuntimeError):
    """Любая ошибка обращения к API Национального каталога."""


class NKNotConfigured(NKError):
    """Не заданы обязательные ключи доступа (NK_API_KEY / NK_PARTY_ID)."""


# ===================== СОСТОЯНИЕ / ДИАГНОСТИКА =====================

def nk_configured():
    return bool(NK_API_KEY and NK_PARTY_ID)


def nk_status():
    """Безопасная диагностика (без раскрытия самих ключей) — для интерфейса."""
    return {
        "configured": nk_configured(),
        "has_api_key": bool(NK_API_KEY),
        "has_party_id": bool(NK_PARTY_ID),
        "base_url": NK_BASE_URL,
    }


# ===================== HTTP-СЛОЙ =====================

def nk_request(method, path, params=None, json_body=None, timeout=60):
    """Базовый вызов API НК. apikey передаётся query-параметром."""
    if not nk_configured():
        raise NKNotConfigured("Не заданы NK_API_KEY и/или NK_PARTY_ID")
    p = dict(params or {})
    p.setdefault("apikey", NK_API_KEY)
    url = NK_BASE_URL + path
    try:
        r = httpx.request(method, url, params=p, json=json_body, timeout=timeout)
    except Exception as e:
        raise NKError(f"Сеть/НК недоступен: {e}")
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:500]
        raise NKError(f"НК {path} {r.status_code}: {detail}")
    try:
        return r.json()
    except Exception:
        return {}


# ===================== СПРАВОЧНИКИ НК =====================

def nk_feed_info():
    # TODO[NK-DOCS]: проверить путь/формат ответа.
    return nk_request("GET", NK_PATHS["feed_info"])


def nk_get_categories():
    # TODO[NK-DOCS]: проверить путь/формат ответа.
    return nk_request("GET", NK_PATHS["categories"])


def nk_get_attributes(category_id):
    # TODO[NK-DOCS]: имя параметра категории может отличаться (cat_id / category_id).
    return nk_request("GET", NK_PATHS["attributes"], params={"cat_id": category_id})


# ===================== СОЗДАНИЕ ТОВАРОВ И ПОЛУЧЕНИЕ ГТИН =====================

def build_nk_product(item):
    """
    Преобразует элемент создания карточки WB в товар формата Национального каталога.

    На вход — элемент из режима «Создать массово» (тот же формат, что уходит в WB):
        { "subjectID": <int>, "variants": [ { vendorCode, title, description,
                                              brand, dimensions, characteristics,
                                              sizes:[{techSize, price, skus}] } ] }

    TODO[NK-DOCS]: НК требует свой набор полей (категория НК, ТНВЭД, бренд,
    обязательные атрибуты товарной группы и т.д.). Здесь — минимальный каркас;
    дополните маппинг по документации «API Национального каталога».
    """
    v = (item.get("variants") or [{}])[0]
    return {
        "party_id": NK_PARTY_ID,
        "vendor_code": v.get("vendorCode"),
        "good_name": v.get("title"),
        "brand": v.get("brand"),
        # TODO[NK-DOCS]: "category": <категория НК>, "tnved": <код ТНВЭД>,
        # TODO[NK-DOCS]: "good_attrs": [ {attr_id, value}, ... ] — обязательные атрибуты.
    }


def nk_create_products(products):
    """
    Создаёт товары (черновики) в Национальном каталоге.
    products — список объектов build_nk_product().

    TODO[NK-DOCS]: уточнить обёртку payload (часто это {"apikey":..,"goods":[...]}
    либо отдельный метод на каждый товар) и формат ответа (good_id и т.п.).
    """
    payload = {"party_id": NK_PARTY_ID, "goods": products}
    return nk_request("POST", NK_PATHS["product_create"], json_body=payload)


def nk_list_products(params=None):
    """Список товаров продавца — отсюда забираем присвоенные ГТИНы."""
    base = {"party_id": NK_PARTY_ID}
    base.update(params or {})
    return nk_request("GET", NK_PATHS["product_list"], params=base)


def _extract_gtin_map(products_response):
    """
    Достаёт соответствие vendor_code -> gtin из ответа НК.

    TODO[NK-DOCS]: подставить реальные имена полей. Ниже — типовые варианты,
    которые встречаются в выгрузках НК; оставлены как ориентир.
    """
    result = {}
    items = []
    if isinstance(products_response, dict):
        items = (products_response.get("result")
                 or products_response.get("goods")
                 or products_response.get("data")
                 or [])
    elif isinstance(products_response, list):
        items = products_response
    for g in items:
        if not isinstance(g, dict):
            continue
        vc = g.get("vendor_code") or g.get("vendorCode") or g.get("good_id")
        gtin = g.get("gtin") or g.get("gtins") or g.get("good_gtin")
        if isinstance(gtin, list):
            gtin = gtin[0] if gtin else None
        if vc and gtin:
            result[str(vc)] = str(gtin)
    return result


def create_gtins_for_items(items, wait_for_gtin=True):
    """
    ГЛАВНЫЙ СЦЕНАРИЙ Варианта Б (КАРКАС).

    1. Преобразовать карточки в товары формата НК.
    2. Создать их в Национальном каталоге.
    3. Дождаться присвоения ГТИНов (опрос nk_list_products).
    4. Вернуть карту vendorCode -> gtin для подстановки в карточки WB.

    Возвращает: { "<vendorCode>": "<gtin>", ... }
    """
    if not nk_configured():
        raise NKNotConfigured("Интеграция с НК не настроена: задайте NK_API_KEY и NK_PARTY_ID.")

    products = [build_nk_product(it) for it in items]
    create_resp = nk_create_products(products)

    # Часть ГТИНов может вернуться сразу в ответе на создание.
    gtin_map = _extract_gtin_map(create_resp)

    # Остальные — дожидаемся через опрос списка товаров.
    if wait_for_gtin:
        want = {str((it.get("variants") or [{}])[0].get("vendorCode"))
                for it in items if (it.get("variants") or [{}])[0].get("vendorCode")}
        for _ in range(GTIN_POLL_ATTEMPTS):
            if want.issubset(set(gtin_map.keys())):
                break
            time.sleep(GTIN_POLL_INTERVAL)
            try:
                listed = nk_list_products()
                gtin_map.update(_extract_gtin_map(listed))
            except NKError:
                pass

    if not gtin_map:
        # Каркас намеренно не молчит: пока маппинг ответа НК не сверен с
        # документацией, честно сообщаем, что шаг требует настройки.
        raise NKError(
            "Товары отправлены в НК, но ГТИНы не распознаны. "
            "Сверьте формат ответа с документацией «API Национального каталога» "
            "и дополните _extract_gtin_map / build_nk_product (TODO[NK-DOCS])."
        )
    return gtin_map
