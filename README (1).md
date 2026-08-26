# tech-doc-parser

**Local two-stage pipeline that turns messy technical PDFs (scans, tables, diagrams) into RAG-ready Markdown chunks. 100% offline — no cloud, no API keys, no Docker. Designed to run on a modest laptop.**

> 🇷 Русская версия: [README_RU.md](README_RU.md)

```
PDF ──▶ parse_pdf.py (stage 1) ──▶ ready_data_*.md ──▶ enrich_chunks.py (stage 2) ──▶ chunks_final.md ──▶ your RAG
```

## Why this exists

General-purpose PDF parsers often fail on real-world technical documents:

- Soviet-era scanned handbooks with multi-level table headers and dual unit systems (SI + MKGSS)
- Pages where tables and diagrams exist only as images, with no text layer
- Modern manuals mixing body text, schematics and spec tables on a single page

This project takes a different approach: whatever the text layer can give is taken verbatim; whatever it cannot (tables, diagrams, broken scans) is read by a **local vision model**; then the result is **semantically enriched** for better retrieval.

## Features

**Stage 1 — parsing (`parse_pdf.py`)**

- Per-page routing: clean text layers extracted verbatim; pages with tables / diagrams / broken text are rendered and sent to the vision model
- Real table detection via PyMuPDF `find_tables()`
- Embedded images cropped and described separately
- Language-agnostic: the model responds in the document's language
- Anti-hallucination prompts: numbers are never "corrected", contradictions are marked instead
- Automatic removal of repetitive generation artifacts (looping)
- Per-page progress saving; interrupted runs resume automatically
- Periodic model unloading from memory for very long batch runs

**Stage 2 — enrichment (`enrich_chunks.py`)**

- Splits Markdown into semantic chunks (configurable size)
- Deduplicates scanned duplicate pages
- Builds an enrichment card for every chunk: summary, fault/process chains, parameters with units, terms and standards, keywords, see-also links
- Automatic per-chunk language detection (RU / EN prompts)
- Retries on API errors; per-chunk progress; auto-resume

## Designed for modest hardware

The project was developed and tested on a typical laptop: **RTX 4060 (8 GB VRAM) + 16 GB RAM shared with Windows 11**. Design decisions for low-end machines:

- The default `qwen3.5:9b` vision model occupies ~6.6 GB VRAM and coexists with the OS; for weaker GPUs, switch to `qwen3.5:4b` in `config.json`
- Default render resolution is **150 DPI** — the practical balance of recognition quality, speed and memory footprint
- The model is periodically unloaded from memory (`RESET_EVERY` / `UNLOAD_EVERY`), so the total page count is effectively unlimited — only time is the limit
- Single process, no Docker, no databases, no extra services

## Requirements

- Windows (`.bat` scripts included; on other OS run the `.py` files directly)
- Python 3.10+ (**Add Python to PATH** during installation)
- [Ollama](https://ollama.com)
- GPU with 8 GB+ VRAM recommended (tested on laptop RTX 4060 / 16 GB RAM). CPU-only is possible but much slower.

## Installation

1. Install Python and Ollama.
2. Run `1_install.bat` — it installs dependencies and downloads the model (one time).

Manual equivalent:

```bash
pip install pymupdf requests
ollama pull qwen3.5:9b
```

## Usage

1. Put your PDF into the project folder.
2. Run `2_parse.bat` → raw Markdown (`ready_data_*.md`).
3. Run `3_enrich.bat` → `chunks_final.md`, the final RAG-ready file.

Both stages run unattended. If interrupted, run the same script again — processing resumes from the last completed page / chunk.

Feed `chunks_final.md` into any RAG system (AnythingLLM, Open WebUI, LangChain, etc.).

## Project files

| File | Purpose |
|---|---|
| `parse_pdf.py` | Stage 1: PDF → Markdown |
| `enrich_chunks.py` | Stage 2: Markdown → enriched chunks |
| `1_install.bat` | One-time setup |
| `2_parse.bat` | Run stage 1 |
| `3_enrich.bat` | Run stage 2 |
| `config.json` | Settings; human hints live in the `_COMMENTS` block |
| `.gitignore` | Keeps your PDFs and generated outputs out of the repo |

## Configuration

Stage 1 reads `config.json` if present (keys starting with `_` are ignored). Defaults:

| Key | Default | Description |
|---|---|---|
| `PDF_FILE` | `""` | File to process; empty = first PDF in folder |
| `OUTPUT_FILE` | `rag_ready_data_v3.md` | Stage 1 output file |
| `PROGRESS_FILE` | `progress_v3.json` | Stage 1 progress file |
| `MODEL_NAME` | `qwen3.5:9b` | Ollama model, shared by both stages. Use `qwen3.5:4b` on GPUs with <8 GB VRAM |
| `OLLAMA_URL` | `http://localhost:11434/api/generate` | Ollama API endpoint |
| `CROP_DPI` | `150` | Render resolution. 150 = default laptop balance; 200–300 = sharper but slower and heavier |
| `MIN_TEXT_CHARS` | `100` | Min chars to treat a page as textual |
| `MIN_CYR_RATIO` | `0.4` | Alphabet-consistency threshold for garbage detection (language-independent) |
| `MIN_IMG_W` / `MIN_IMG_H` | `100` / `80` | Min embedded image size to crop |
| `MARGIN` | `10` | Crop margin, points |
| `RESET_EVERY` | `50` | Unload the model every N pages |

Stage 2 constants (top of `enrich_chunks.py`):

| Constant | Default | Description |
|---|---|---|
| `CHUNK_MAX_CHARS` | `6000` | Max characters per chunk |
| `UNLOAD_EVERY` | `20` | Unload the model every N chunks (0 = off) |
| `MAX_RETRIES` | `2` | Retries per chunk on API error |

## Output format

Stage 1 (intermediate):

```
--- PAGE PDF 50 / BOOK 90 ---
[PAGE -> VISION]
| Density at +15°C, kg/m³ | Viscosity at 0°C | ... |
| 1025 | 31.36 (3.20) | ... |
```

Stage 2 (final):

```
--- CHUNK 0042 | PAGES 50-51 ---
[ENRICHMENT]
SUMMARY: ...
CHAINS: symptom -> cause -> check -> solution
PARAMETERS: ...
TERMS: ...
KEYWORDS: ...
SEE ALSO: ...

[TEXT]
<original chunk text>
```

## Limitations

- Models ≤4B parameters may misparse multi-level tables; 9B+ recommended when VRAM allows
- Very small fonts (<6 pt): increase `CROP_DPI` to 200–300
- Enrichment quality depends on model size
- On 8 GB VRAM laptops, close other GPU-heavy applications during a run

## Roadmap

- One-click installer (auto-download of Ollama + model)
- Executable build via PyInstaller
- Optional integration with external LLM APIs for the hardest pages

## License

Free to use for any purpose.
