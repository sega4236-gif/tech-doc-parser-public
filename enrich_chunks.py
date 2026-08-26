import os
import re
import json
import glob
import time
import requests

# ==========================================
# НАСТРОЙКИ
# ==========================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Модель и URL берём из config.json, чтобы оба этапа работали на одной модели
_CFG = {"MODEL_NAME": "qwen3.5:9b", "OLLAMA_URL": "http://localhost:11434/api/generate"}
_cfg_path = os.path.join(SCRIPT_DIR, "config.json")
if os.path.exists(_cfg_path):
    try:
        with open(_cfg_path, encoding="utf-8") as f:
            for k, v in json.load(f).items():
                if k in _CFG:
                    _CFG[k] = v
    except Exception:
        pass

INPUT_MD = ""  # "" = искать любой md в папке скрипта; или впиши имя файла явно
OUTPUT_MD = os.path.join(SCRIPT_DIR, "chunks_final.md")
PROGRESS_FILE = os.path.join(SCRIPT_DIR, "progress_enrich.json")
MODEL_NAME = _CFG["MODEL_NAME"]
OLLAMA_URL = _CFG["OLLAMA_URL"]
CHUNK_MAX_CHARS = 6000  # максимум символов в чанке
UNLOAD_EVERY = 20  # выгружать модель каждые N чанков (0 = выкл)
MAX_RETRIES = 2

# ==========================================
# ПРОМТЫ: РУССКИЙ И АНГЛИЙСКИЙ
# ==========================================
PROMPT_ENRICH_RU = """Ты — инженер-эксперт по промышленной и технической документации (промышленное оборудование, механика, энергетика, климатическая техника).
Тебе дан фрагмент технической документации. Составь карточку обогащения для поисковой базы знаний.
ФОРМАТ (разделы, которых нет во фрагменте, — пропускай):
СУММАРИ: 2-4 предложения: какое оборудование/узел/процесс описан, назначение, область применения.
ЦЕПИ: логические цепочки, по одной на строке:
неисправности: "симптом -> причина -> проверка/диагностика -> решение";
процессы/режимы: "условие/действие -> следствие";
нормы/допуски: "параметр -> норма -> что будет при нарушении".
ПАРАМЕТРЫ: ключевые числа, допуски, нормы с единицами измерения, кратко списком.
ТЕРМИНЫ: марки, обозначения, ГОСТ/ТУ/ISO, номера таблиц/рисунков/позиций из фрагмента.
КЛЮЧЕВЫЕ СЛОВА: 8-12 поисковых слов и синонимов (в т.ч. как это назовёт механик на практике).
СМ. ТАКЖЕ: смежные узлы/системы/процессы, с которыми связан фрагмент.
ПРАВИЛА:
Если таблица в источнике развалилась и ты НЕ уверена, какое значение к какой строке/колонке относится,
НЕ приписывай значения конкретным маркам. Перечисли значения как в источнике
или пометь [соответствие строкам неясно]. Не выдумывай сопоставления.
Не повторяй инструкции и правила в конце ответа.
Только факты из фрагмента + общеизвестные инженерные связи. Не выдумывай.
Числа не исправляй; сомнительное значение помечай [прим.: возможно опечатка].
Кратко, без воды и рассуждений. Начинай сразу с "СУММАРИ:".
Пиши карточку целиком НА РУССКОМ языке."""

PROMPT_ENRICH_EN = """You are an expert engineer in industrial and technical documentation (industrial equipment, mechanical, energy, HVAC).
You are given a fragment of technical documentation. Create an enrichment card for a search knowledge base.
FORMAT (skip sections that are not present in the fragment):
SUMMARY: 2-4 sentences: what equipment/unit/process is described, purpose, application area.
CHAINS: logical chains, one per line:
faults: "symptom -> cause -> check/diagnostics -> solution";
processes/modes: "condition/action -> consequence";
norms/tolerances: "parameter -> norm -> what happens if violated".
PARAMETERS: key numbers, tolerances, norms with units, brief list.
TERMS: brands, designations, standards (ISO/EN/ASHRAE etc.), table/figure/item numbers from the fragment.
KEYWORDS: 8-12 search words and synonyms (including how a mechanic would say it in practice).
SEE ALSO: related units/systems/processes connected to the fragment.
RULES:
If a table in the source is broken and you are NOT sure which value belongs to which row/column,
do NOT assign values to specific models. List values as in the source
or mark [row correspondence unclear]. Do not invent mappings.
Do not repeat the instructions and rules at the end of the answer.
Only facts from the fragment + well-known engineering connections. Do not invent.
Do not correct numbers; mark a doubtful value with [note: possible typo].
Brief, no fluff, no reasoning. Start immediately with "SUMMARY:".
Write the ENTIRE card in ENGLISH."""

# Маркер страницы: RU/EN, с "PDF"/"КНИГА" и любыми хвостами в скобках
PAGE_RE = re.compile(
    r"(?m)^-{3,}\s*(?:СТРАНИЦ[АЫЕ]|PAGE)\s*(?:PDF\s*)?(\d+)[^\n]*$",
    re.IGNORECASE,
)


# ==========================================
# ПОИСК ВХОДНОГО MD
# ==========================================
def find_input_md():
    if INPUT_MD and os.path.exists(INPUT_MD):
        return INPUT_MD
    out_name = os.path.basename(OUTPUT_MD).lower()
    cands = []
    for pat in ("rag_ready_data*.md", "*.md"):
        for p in sorted(glob.glob(os.path.join(SCRIPT_DIR, pat))):
            if os.path.basename(p).lower() == out_name:
                continue  # не берём собственный выходной файл
            if p not in cands:
                cands.append(p)
        if cands:
            break
    if not cands:
        return None
    # берём самый большой — это и есть настоящий выход распознавалки
    cands.sort(key=lambda p: os.path.getsize(p), reverse=True)
    return cands[0]


# ==========================================
# РАЗБОР СТРАНИЦ
# ==========================================
def detect_lang(text):
    """Кириллица против латиницы: выбираем язык карточки."""
    cyr = sum(1 for c in text if "а" <= c.lower() <= "я" or c.lower() == "ё")
    lat = sum(1 for c in text if "a" <= c.lower() <= "z")
    return "ru" if cyr >= lat else "en"


def clean_page(text):
    """Склейка переносов и мусорных пробелов — лучше и обогащение, и эмбеддинги."""
    text = re.sub(r"([а-яёa-z0-9])-\s*\n\s*([а-яёa-z])", r"\1\2", text, flags=re.I)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_pages(md_text):
    pages = []
    prev_text = None
    it = iter(PAGE_RE.split(md_text)[1:])
    for num, body in zip(it, it):
        body = clean_page(body)
        if not body:
            continue
        if body == prev_text:  # дедубль: в скане бывают страницы-дубликаты
            continue
        prev_text = body
        pages.append({"page": int(num), "text": body})
    if not pages and md_text.strip():
        print("⚠️ Маркеры страниц не найдены — режу весь файл как один поток.")
        pages = [{"page": 0, "text": md_text.strip()}]
    return pages


def split_big(text, limit):
    if len(text) <= limit:
        return [text]
    pieces, cur = [], ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) > limit:  # слишком длинный абзац — режем по строкам
            if cur:
                pieces.append(cur)
                cur = ""
            cur2 = ""
            for ln in para.split("\n"):
                if len(cur2) + len(ln) + 1 > limit:
                    pieces.append(cur2)
                    cur2 = ln
                else:
                    cur2 = cur2 + "\n" + ln if cur2 else ln
            if cur2:
                pieces.append(cur2)
            continue
        if len(cur) + len(para) + 2 > limit:
            pieces.append(cur)
            cur = para
        else:
            cur = cur + "\n\n" + para if cur else para
    if cur:
        pieces.append(cur)
    return pieces


def build_chunks(pages):
    chunks = []
    cur_text, cur_pages = "", []

    def flush():
        nonlocal cur_text, cur_pages
        if cur_text.strip():
            chunks.append({"pages": cur_pages, "text": cur_text.strip()})
        cur_text, cur_pages = "", []

    for p in pages:
        for piece in split_big(p["text"], CHUNK_MAX_CHARS):
            if cur_text and len(cur_text) + len(piece) + 2 > CHUNK_MAX_CHARS:
                flush()
            cur_text = cur_text + "\n\n" + piece if cur_text else piece
            cur_pages.append(p["page"])
    flush()
    # мелкий хвост приклеиваем к предыдущему чанку
    if len(chunks) > 1 and len(chunks[-1]["text"]) < 800:
        last = chunks.pop()
        chunks[-1]["text"] += "\n\n" + last["text"]
        chunks[-1]["pages"] += last["pages"]
    return chunks


def page_label(pages):
    u = sorted(set(pages))
    if u == [0]:
        return "б/н"
    return str(u[0]) if len(u) == 1 else f"{u[0]}-{u[-1]}"


def remove_llm_loops(text, max_block=8, max_repeats=2):
    """Режет 'заевшую пластинку' в ответе модели."""
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
            j, reps = i + b, 1
            while j + b <= n and norm[j : j + b] == block:
                reps += 1
                j += b
            if reps > max_repeats:
                cut_from = i + b * max_repeats
                break
        i += 1
    if cut_from is None:
        return text
    return (
        "\n".join(lines[:cut_from]).rstrip()
        + "\n[прим.: повторяющийся фрагмент обрезан]"
    )


# ==========================================
# ЗАПРОС К OLLAMA
# ==========================================
def ask_model(chunk_text, label):
    lang = detect_lang(chunk_text)
    if lang == "ru":
        prompt = PROMPT_ENRICH_RU
        head = f"Страницы фрагмента: {label}\n\nФРАГМЕНТ:\n"
    else:
        prompt = PROMPT_ENRICH_EN
        head = f"Fragment pages: {label}\n\nFRAGMENT:\n"
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt + "\n\n" + head + chunk_text,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": 2048},
    }
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.post(OLLAMA_URL, json=payload, timeout=600)
            r.raise_for_status()
            out = r.json().get("response", "").strip()
            if out:
                return remove_llm_loops(out)
        except Exception as e:
            print(f"   ⚠️ попытка {attempt + 1} не удалась: {e}")
            time.sleep(3)
    return "[ошибка обогащения]"


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
        print("   🔄 модель выгружена из памяти")
    except Exception:
        pass


# ==========================================
# ОСНОВНОЙ ЦИКЛ
# ==========================================
def main():
    input_md = find_input_md()
    if not input_md:
        print("❌ Не найден ни один входной .md в папке скрипта!")
        return
    with open(input_md, encoding="utf-8") as f:
        md_text = f.read()

    pages = parse_pages(md_text)
    chunks = build_chunks(pages)
    total = len(chunks)
    print(f"📄 Вход: {input_md} | страниц: {len(pages)} | чанков: {total}")
    if total == 0:
        print("❌ Нечего обогащать — файл пустой.")
        return

    last = 0
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, encoding="utf-8") as f:
                last = json.load(f).get("last_chunk", 0)
        except Exception:
            last = 0
    if last:
        print(f"🔄 продолжаем с чанка {last + 1}")

    with open(OUTPUT_MD, "a" if last else "w", encoding="utf-8") as f:
        if not last:
            f.write(
                f"# Чанки с обогащением (источник: {os.path.basename(input_md)})\n\n"
            )

    since_unload = 0
    for i, ch in enumerate(chunks, 1):
        if i <= last:
            continue
        label = page_label(ch["pages"])
        print(f"[{i}/{total}] страницы {label} ...")
        card = ask_model(ch["text"], label)
        with open(OUTPUT_MD, "a", encoding="utf-8") as f:
            f.write(f"--- ЧАНК {i:04d} | СТРАНИЦЫ {label} ---\n")
            f.write("[ОБОГАЩЕНИЕ]\n" + card + "\n\n")
            f.write("[ТЕКСТ]\n" + ch["text"] + "\n\n")
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_chunk": i}, f)
        print("  ✅ готово")
        since_unload += 1
        if UNLOAD_EVERY and since_unload >= UNLOAD_EVERY:
            unload_model()
            since_unload = 0

    print("\n🎉 ГОТОВО, БРО!")


if __name__ == "__main__":
    main()
