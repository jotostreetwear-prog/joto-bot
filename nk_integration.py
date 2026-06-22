"""
Интеграция с Национальным каталогом (Честный ЗНАК) — Вариант Б.

Подтверждённый по документации ЦРПТ процесс получения ГТИНов:
    1. generate-gtins  → сгенерировать черновики кодов товаров (ГТИНы) и получить их.
    2. создать карточку «Единица товара» с этим ГТИНом (is_tech_gtin = false).
    3. ГТИНы подставляются как баркоды (sizes[].skus) при создании карточек на WB.

⚠️  ПУТИ ЭНДПОИНТОВ И ФОРМАТ ТЕЛ ЗАПРОСОВ нужно сверить с документацией в личном
    кабинете «Честный ЗНАК»: товарная группа → «Помощь» → «API» → «API
    Национального каталога» (docs.crpt.ru/gismt/API_НК — доступна из ЛК).
    Подтверждены НАЗВАНИЯ методов и флаг is_tech_gtin; точные URL/схемы тел —
    помечены TODO[NK-DOCS]. Честный ЗНАК — государственная система, поэтому до
    боевого запуска формат запросов обязательно сверить с офиц. документацией.

Переменные окружения:
    NK_API_KEY   — apikey Национального каталога (ЛК → «Ключи API» → «Создать ключ»)
    NK_PARTY_ID  — идентификатор владельца товара (party_id)
    NK_TOKEN     — bearer-токен ГИС МТ (если в вашем контуре авторизация по токену,
                   а не по apikey; живёт ~10 часов, опционально)
    NK_BASE_URL  — базовый URL API НК (сверьте с документацией)
"""

import os
import time
import httpx

# ===================== КОНФИГ =====================

NK_API_KEY = os.environ.get("NK_API_KEY", "").strip()
NK_PARTY_ID = os.environ.get("NK_PARTY_ID", "").strip()
NK_TOKEN = os.environ.get("NK_TOKEN", "").strip()  # опционально: bearer ГИС МТ
# TODO[NK-DOCS]: подставьте базовый URL из документации НК (ЛК «Честный ЗНАК»).
NK_BASE_URL = os.environ.get("NK_BASE_URL", "https://апи.национальный-каталог.рф").strip().rstrip("/")

# Названия методов generate-gtins / product-create подтверждены документацией ЦРПТ.
# TODO[NK-DOCS]: сверьте полные пути (префикс версии /v3 или /v4) с документацией ЛК.
NK_PATHS = {
    "feed_info": "/v4/feed-info",            # информация об аккаунте/правах
    "categories": "/v3/categories",          # дерево категорий НК
    "attributes": "/v3/attributes",          # атрибуты категории
    "generate_gtins": "/v3/generate-gtins",  # генерация черновиков ГТИН
    "product_create": "/v3/product-create",  # создание карточки «Единица товара»
    "product_list": "/v4/product-list",      # список товаров (статусы/ГТИНы) — подтверждён v4
}

# Ожидание присвоения/активации ГТИН (если нужно опрашивать список товаров).
GTIN_POLL_ATTEMPTS = 10
GTIN_POLL_INTERVAL = 3  # секунд


# ===================== ИСКЛЮЧЕНИЯ =====================

class NKError(RuntimeError):
    """Любая ошибка обращения к API Национального каталога."""


class NKNotConfigured(NKError):
    """Не заданы обязательные ключи доступа (NK_API_KEY / NK_PARTY_ID)."""


# ===================== СОСТОЯНИЕ / ДИАГНОСТИКА =====================

def nk_configured():
    # party_id необязателен — для запроса своих товаров обычно хватает apikey
    return bool(NK_API_KEY or NK_TOKEN)


def nk_status():
    """Безопасная диагностика (без раскрытия ключей) — для интерфейса."""
    return {
        "configured": nk_configured(),
        "has_api_key": bool(NK_API_KEY),
        "has_token": bool(NK_TOKEN),
        "has_party_id": bool(NK_PARTY_ID),
        "base_url": NK_BASE_URL,
    }


# ===================== HTTP-СЛОЙ =====================

def nk_request(method, path, params=None, json_body=None, timeout=60):
    """
    Базовый вызов API НК.
    apikey передаётся query-параметром; если задан NK_TOKEN — добавляется
    заголовок Authorization: Bearer (для контуров с токен-авторизацией ГИС МТ).
    """
    if not nk_configured():
        raise NKNotConfigured("Не заданы NK_API_KEY (или NK_TOKEN) и/или NK_PARTY_ID")
    p = dict(params or {})
    if NK_API_KEY:
        p.setdefault("apikey", NK_API_KEY)
    headers = {}
    if NK_TOKEN:
        headers["Authorization"] = f"Bearer {NK_TOKEN}"
    url = NK_BASE_URL + path
    try:
        r = httpx.request(method, url, params=p, json=json_body, headers=headers, timeout=timeout)
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
    return nk_request("GET", NK_PATHS["feed_info"])


def nk_get_categories():
    return nk_request("GET", NK_PATHS["categories"])


def nk_get_attributes(category_id):
    # TODO[NK-DOCS]: имя параметра категории может отличаться (cat_id / category_id).
    return nk_request("GET", NK_PATHS["attributes"], params={"cat_id": category_id})


# ===================== ШАГ 1: ГЕНЕРАЦИЯ ГТИН =====================

def nk_generate_gtins(count):
    """
    Генерирует `count` черновиков ГТИН и возвращает список кодов (строк).

    TODO[NK-DOCS]: сверьте тело запроса и формат ответа. По документации метод
    generate-gtins возвращает сгенерированные коды товаров; типовые варианты
    обёртки ответа учтены в разборе ниже.
    """
    body = {"party_id": NK_PARTY_ID, "count": int(count)}
    resp = nk_request("POST", NK_PATHS["generate_gtins"], json_body=body)
    return _extract_gtins(resp)


def _extract_gtins(resp):
    """Достаёт список ГТИН из ответа generate-gtins (учёт разных обёрток)."""
    if isinstance(resp, list):
        items = resp
    elif isinstance(resp, dict):
        items = (resp.get("gtins") or resp.get("result") or resp.get("data")
                 or resp.get("codes") or [])
    else:
        items = []
    out = []
    for it in items:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            g = it.get("gtin") or it.get("code") or it.get("good_id")
            if g:
                out.append(str(g))
    return out


# ===================== ШАГ 2: СОЗДАНИЕ КАРТОЧКИ «ЕДИНИЦА ТОВАРА» =====================

def build_nk_unit(gtin, item):
    """
    Тело карточки «Единица товара» для product-create.
    gtin — ранее сгенерированный код; is_tech_gtin = false (подтверждено докой).

    TODO[NK-DOCS]: дополните обязательными атрибутами товарной группы (категория
    НК, бренд, ТНВЭД, good_attrs и т.д.) — список берётся из nk_get_attributes().
    """
    v = (item.get("variants") or [{}])[0]
    return {
        "party_id": NK_PARTY_ID,
        "gtin": gtin,
        "is_tech_gtin": False,
        "good_name": v.get("title"),
        "vendor_code": v.get("vendorCode"),
        "brand": v.get("brand"),
        # TODO[NK-DOCS]: "category": <категория НК>, "tnved": <ТНВЭД>,
        # TODO[NK-DOCS]: "good_attrs": [ {attr_id, value}, ... ] — обязательные атрибуты.
    }


def nk_create_unit(gtin, item):
    body = build_nk_unit(gtin, item)
    return nk_request("POST", NK_PATHS["product_create"], json_body=body)


# ===================== АВТОЗАГРУЗКА ТОВАРОВ ИЗ НК (без файла) =====================
# ID атрибутов НК взяты из строки кодов шаблона импорта (см. реальный файл ЧЗ).
NK_ATTRS = {"article": 13914, "size": 35, "color": 36, "composition": 2483, "tnved": 13933}

def _extract_goods(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        r = data.get("result")
        if isinstance(r, dict):
            return r.get("goods") or r.get("data") or r.get("products") or []
        if isinstance(r, list):
            return r
        return data.get("goods") or data.get("data") or data.get("products") or []
    return []

def _good_attrs_map(g):
    out = {}
    attrs = g.get("good_attrs") or g.get("attrs") or g.get("attributes") or []
    for a in attrs:
        if not isinstance(a, dict):
            continue
        aid = a.get("attr_id") or a.get("id")
        val = a.get("values") if "values" in a else a.get("value")
        if isinstance(val, list):
            parts = []
            for v in val:
                parts.append(str(v.get("value")) if isinstance(v, dict) else str(v))
            val = ", ".join(p for p in parts if p)
        if aid is not None:
            out[aid] = (str(val).strip() if val is not None else "")
    return out

def _good_gtin(g):
    gt = g.get("gtin") or g.get("gtins")
    if isinstance(gt, list):
        if gt and isinstance(gt[0], dict):
            return str(gt[0].get("gtin") or "")
        if gt:
            return str(gt[0])
        return ""
    return str(gt) if gt else ""

def nk_fetch_products(limit=1000):
    """
    Тянет товары продавца из Национального каталога и нормализует строки
    в тот же формат, что даёт парсер файла:
        [{article, size, gtin, name, color, composition, tnved}, ...]

    TODO[NK-DOCS]: сверьте путь product-list, имя параметра поставщика и
    структуру ответа (goods / good_attrs / gtins) с документацией в ЛК ЧЗ.
    """
    if not nk_configured():
        raise NKNotConfigured("Интеграция с НК не настроена: задайте NK_API_KEY и NK_PARTY_ID.")
    params = {"limit": limit}
    if NK_PARTY_ID:
        params["suppliers[]"] = NK_PARTY_ID
    data = nk_request("GET", NK_PATHS["product_list"], params=params)
    rows = []
    for g in _extract_goods(data):
        if not isinstance(g, dict):
            continue
        attrs = _good_attrs_map(g)
        rows.append({
            "article": attrs.get(NK_ATTRS["article"], ""),
            "size": attrs.get(NK_ATTRS["size"], ""),
            "color": attrs.get(NK_ATTRS["color"], ""),
            "composition": attrs.get(NK_ATTRS["composition"], ""),
            "tnved": attrs.get(NK_ATTRS["tnved"], ""),
            "name": g.get("good_name") or g.get("name") or "",
            "gtin": _good_gtin(g),
        })
    return rows


# ===================== ГЛАВНЫЙ СЦЕНАРИЙ =====================

def create_gtins_for_items(items, create_cards=True):
    """
    Возвращает карту  { "<vendorCode>": "<gtin>", ... }.

    1. Генерирует столько ГТИН, сколько товаров.
    2. (если create_cards) создаёт по каждому карточку «Единица товара» в НК.
    3. Возвращает соответствие артикул → ГТИН для подстановки в карточки WB.
    """
    if not nk_configured():
        raise NKNotConfigured("Интеграция с НК не настроена: задайте NK_API_KEY и NK_PARTY_ID.")

    vendor_codes = [str((it.get("variants") or [{}])[0].get("vendorCode") or "").strip()
                    for it in items]
    if any(not vc for vc in vendor_codes):
        raise NKError("У всех товаров должен быть заполнен артикул (vendorCode).")

    gtins = nk_generate_gtins(len(items))
    if len(gtins) < len(items):
        raise NKError(
            f"НК вернул {len(gtins)} ГТИН на {len(items)} товаров. "
            "Сверьте ответ generate-gtins с документацией (TODO[NK-DOCS])."
        )

    gtin_map = {}
    errors = []
    for item, vc, gtin in zip(items, vendor_codes, gtins):
        gtin_map[vc] = gtin
        if create_cards:
            try:
                nk_create_unit(gtin, item)
            except NKError as e:
                errors.append(f"{vc}: {e}")

    if errors and len(errors) == len(items):
        # Все карточки не создались — значит формат тела не сверён с докой.
        raise NKError(
            "ГТИНы сгенерированы, но ни одна карточка «Единица товара» не создана. "
            "Сверьте build_nk_unit / product-create с документацией НК (TODO[NK-DOCS]). "
            "Первая ошибка: " + errors[0]
        )
    return gtin_map
