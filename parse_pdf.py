import os
import re
import json
import base64
import glob
import requests
import fitz  # PyMuPDF

# ==========================================
# НАСТРОЙКИ
# ==========================================
CONFIG = {
    "PDF_FILE": "",
    "OUTPUT_FILE": "rag_ready_data_v3.md",
    "PROGRESS_FILE": "progress_v3.json",
    "MODEL_NAME": "qwen3.5:9b",
    "OLLAMA_URL": "http://localhost:11434/api/generate",
    "CROP_DPI": 150,
    "MIN_TEXT_CHARS": 100,
    "MIN_CYR_RATIO": 0.4,
    "MIN_IMG_W": 100,
    "MIN_IMG_H": 80,
    "MARGIN": 10,
    "RESET_EVERY": 50,
}
if os.path.exists("config.json"):
    try:
        with open("config.json", encoding="utf-8") as f:
            user_config = json.load(f)
            for key, value in user_config.items():
                if not key.startswith("_"):
                    CONFIG[key] = value
        print("✅ config.json загружен")
    except Exception as e:
        print(f"⚠️ Не удалось прочитать config.json: {e}")

PDF_FILE = CONFIG["PDF_FILE"]
OUTPUT_FILE = CONFIG["OUTPUT_FILE"]
PROGRESS_FILE = CONFIG["PROGRESS_FILE"]
MODEL_NAME = CONFIG["MODEL_NAME"]
OLLAMA_URL = CONFIG["OLLAMA_URL"]
CROP_DPI = CONFIG["CROP_DPI"]
MIN_TEXT_CHARS = CONFIG["MIN_TEXT_CHARS"]
MIN_CYR_RATIO = CONFIG["MIN_CYR_RATIO"]
MIN_IMG_W = CONFIG["MIN_IMG_W"]
MIN_IMG_H = CONFIG["MIN_IMG_H"]
MARGIN = CONFIG["MARGIN"]
RESET_EVERY = CONFIG["RESET_EVERY"]

# ==========================================
# ПРОМТЫ (С защитой от галлюцинаций)
# ==========================================
PROMPT_IMG = """Ты — эксперт по оцифровке технической документации.
Отвечай ТОЛЬКО на том языке, на котором написан документ.
ПРАВИЛА ДЛЯ ТАБЛИЦ:
Если заголовки ВЛОЖЕННЫЕ (несколько строк сверху), разверни в ОДНУ строку:
БЫЛО: [Вязкость → 0°C | -10°C | -15°C]
СТАЛО: [| Вязкость при 0°C | Вязкость при -10°C | Вязкость при -15°C |]
Каждая ячейка данных = ОДНО значение. НЕ размазывай одно число по нескольким ячейкам.
Если не можешь разобрать структуру таблицы — напиши "[Таблица не распознана]" и опиши текстом что видишь.
ПРАВИЛА ДЛЯ ГРАФИКОВ/ДИАГРАММ:
Опиши оси, подписи, ключевые точки.
НЕ пытайся угадать числа, которых нет на графике.
ФОРМАТ: СРАЗУ начинай с "[Тип: ...]". Без рассуждений, без вступлений.
ЗАПРЕТЫ:
НЕ добавляй от себя примечания, комментарии, "обратите внимание".
НЕ исправляй числа/формулы. Если выглядит противоречиво — ставь после него [прим.: возможно опечатка в оригинале].
НЕ выдумывай данные, которых нет на картинке."""

PROMPT_PAGE = """Ты — эксперт по оцифровке технической документации (промышленное оборудование, справочники, инструкции).
Отвечай ТОЛЬКО на том языке, на котором написан документ.
Перепиши текст страницы дословно. Таблицы — в Markdown, схемы — списком элементов.
ПРАВИЛА:
СРАЗУ начинай с текста. Без вступлений и рассуждений.
НЕ добавляй от себя примечания и комментарии — только то, что есть в оригинале.
Противоречивые числа/формулы НЕ исправляй → помечай [прим.: возможно опечатка в оригинале]."""


# ==========================================
# ПОМОЩНИКИ
# ==========================================
def clean_ocr_text(text):
    """Чистка текстового слоя от артефактов OCR."""
    text = re.sub(r"([а-яА-ЯёЁa-zA-Z])-[\s\n]+([а-яА-ЯёЁa-zA-Z])", r"\1\2", text)
    text = re.sub(r"^\s*[•\-*]\s+", " ", text, flags=re.MULTILINE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


def alphabet_ratio(text):
    """Доля преобладающего алфавита (кириллица ИЛИ латиница) среди всех букв.
    Универсально для русских И английских документов. Кракозябры дадут мало."""
    letters = [c.lower() for c in text if c.isalpha()]
    if not letters:
        return 0.0
    cyr = sum(1 for c in letters if "а" <= c <= "я" or c == "ё")
    lat = sum(1 for c in letters if "a" <= c <= "z")
    return max(cyr, lat) / len(letters)


def is_table_or_diagram_page(page, text):
    """Страница в визию, только если там РЕАЛЬНАЯ таблица или плотная сетка цифр."""
    # Надёжный способ: встроенный детектор PyMuPDF ищет настоящие таблицы по сетке
    try:
        if len(page.find_tables().tables) > 0:
            return True
    except Exception:
        pass  # старая версия PyMuPDF — тогда работаем по запасному варианту
    # Запасной вариант: страница-сплошная спецификация (очень много цифр)
    if len(text) > 100:
        digits = sum(1 for c in text if c.isdigit())
        if digits / len(text) > 0.25:
            return True
    return False


def extract_book_page(text):
    """Парсинг номера страницы книги из головы или хвоста текста."""
    tail = text[-100:].strip()
    match = re.search(r"(?:^|\n)\s*(\d{1,4})\s*(?:\n|$)", tail)
    if match:
        return match.group(1)
    head = text[:100].strip()
    match = re.search(r"(?:^|\n)\s*(\d{1,4})\s*(?:\n|$)", head)
    if match:
        return match.group(1)
    return None


def remove_llm_loops(text, max_repeats=2, max_block=8):
    """Режет 'заевшую пластинку': повторяющиеся БЛОКИ строк."""
    if not text:
        return text
    lines = text.split("\n")
    n = len(lines)
    norm = [l.strip() for l in lines]
    cut_from = None
    i = 0
    while i < n and cut_from is None:
        for b in range(1, max_block + 1):
            if i + b > n:
                break
            block = norm[i : i + b]
            if not any(block):
                continue
            j = i + b
            reps = 1
            while j + b <= n and norm[j : j + b] == block:
                reps += 1
                j += b
            if reps > max_repeats:
                cut_from = i + b * max_repeats
                break
        i += 1
    if cut_from is None:
        return text
    kept = "\n".join(lines[:cut_from]).rstrip()
    return kept + "\n\n[прим.: повторяющийся фрагмент обрезан — зацикливание модели]"


def ask_vision(png_bytes, prompt):
    """Один запрос = одна картинка."""
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "images": [base64.b64encode(png_bytes).decode()],
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": 8192,
        },
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=600)
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        return remove_llm_loops(raw)
    except requests.exceptions.Timeout:
        return "[Ошибка: превышено время ожидания Ollama]"
    except Exception as e:
        return f"[Ошибка API: {e}]"


def unload_model():
    """Полностью выгрузить модель из ОЗУ/видеопамяти."""
    try:
        requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "prompt": "ok",
                "stream": False,
                "think": False,
                "keep_alive": 0,
            },
            timeout=60,
        )
        print("   🔄 Модель выгружена из памяти (ОЗУ освобождена)")
    except Exception:
        pass


def image_rects(page):
    """Рамки встроенных картинок, кроме скана во всю страницу."""
    rects = []
    pw, ph = page.rect.width, page.rect.height
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            if r.width >= MIN_IMG_W and r.height >= MIN_IMG_H:
                if not (r.width >= pw * 0.9 and r.height >= ph * 0.9):
                    rects.append(r)
    merged = []
    for r in rects:
        r = fitz.Rect(r.x0 - MARGIN, r.y0 - MARGIN, r.x1 + MARGIN, r.y1 + MARGIN)
        for m in merged:
            if m.intersects(r):
                m.x0 = min(m.x0, r.x0)
                m.y0 = min(m.y0, r.y0)
                m.x1 = max(m.x1, r.x1)
                m.y1 = max(m.y1, r.y1)
                break
        else:
            merged.append(r)
    return merged


# ==========================================
# ОСНОВНОЙ ЦИКЛ
# ==========================================
def main():
    pdfs = [PDF_FILE] if PDF_FILE else sorted(glob.glob("*.pdf"))
    if not pdfs or not os.path.exists(pdfs[0]):
        print("❌ PDF не найден в папке!")
        return
    pdf_file = pdfs[0]
    doc = fitz.open(pdf_file)
    total = doc.page_count
    last = 0
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, encoding="utf-8") as f:
                last = json.load(f).get("last_page", 0)
        except Exception:
            last = 0
    with open(OUTPUT_FILE, "a" if last > 0 else "w", encoding="utf-8") as f:
        if last == 0:
            f.write(f"# RAG данные v3: {pdf_file}\n\n")
    print(f"📄 {pdf_file} | страниц: {total} | продолжаем с {last + 1}\n")
    pages_since_reset = 0
    for n in range(total):
        page_num = n + 1
        if page_num <= last:
            continue
        page = doc[n]
        raw_text = page.get_text()
        clean_text = clean_ocr_text(raw_text)
        good_text = (
            len(clean_text) >= MIN_TEXT_CHARS
            and alphabet_ratio(clean_text) >= MIN_CYR_RATIO
        )
        book_page = extract_book_page(raw_text)
        if book_page:
            header = f"\n--- СТРАНИЦА PDF {page_num} / КНИГА {book_page} ---\n"
        else:
            header = f"\n--- СТРАНИЦА PDF {page_num} ---\n"
        block = [header]
        has_table = is_table_or_diagram_page(page, raw_text)
        if good_text and not has_table:
            block.append("[ТЕКСТОВЫЙ СЛОЙ]\n" + clean_text + "\n")
            mode = "текст"
            for i, r in enumerate(image_rects(page), 1):
                pix = page.get_pixmap(dpi=CROP_DPI, clip=r)
                block.append(
                    f"[ИЗОБРАЖЕНИЕ {i} -> ВИЗУАЛ]\n"
                    + ask_vision(pix.tobytes("png"), PROMPT_IMG)
                    + "\n"
                )
                mode += f"+схема{i}"
        else:
            pix = page.get_pixmap(dpi=CROP_DPI)
            block.append(
                "[СТРАНИЦА -> ВИЗУАЛ]\n"
                + ask_vision(pix.tobytes("png"), PROMPT_PAGE)
                + "\n"
            )
            mode = "визуал (таблица/схема)" if has_table else "визуал (плохой текст)"
        with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
            f.write("".join(block))
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_page": page_num}, f)
        pages_since_reset += 1
        print(f"[{page_num}/{total}] режим: {mode}")
        if pages_since_reset >= RESET_EVERY:
            unload_model()
            pages_since_reset = 0
    doc.close()
    print("\n🎉 ГОТОВО! База собрана, можно грузить в RAG.")


if __name__ == "__main__":
    main()
