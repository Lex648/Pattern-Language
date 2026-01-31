import json
import os
import re
import tempfile
from datetime import datetime

import streamlit as st
import dropbox
from dotenv import load_dotenv
from unidecode import unidecode

try:
    import pypandoc
except Exception:
    pypandoc = None

try:
    from fpdf import FPDF
except Exception:
    FPDF = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


APP_TITLE = "Pattern Language Machine"
MODEL_NAME = "gpt-4o"

PDF_CHAR_REPLACEMENTS = {
    "—": "-",
    "–": "-",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "…": "...",
    "•": "-",
}

V4_SYSTEM_PROMPT = """
Je bent een diepgravende academische essayist in de traditie van Christopher Alexander
(A Pattern Language). Je doel is niet om te adviseren, maar om een denkkader te bieden.

Stijl & Toon:

Taal: Poëtisch, observationeel en tijdloos; helder, ritmisch en onderzoekend.

Vermijd lijstjes en bullet points; gebruik uitsluitend 'long read' tekstblokken.

Geen marketingtaal of consultancy-jargon. Liever kijken dan verklaren.

Let op ritmische variatie: elk patroon heeft een eigen adem en tempo.

Strikte Anatomie per Patroon:

Titel: Beeldend en krachtig (geen dubbele punten).

The Conflict: Eén vetgedrukte probleemstelling die de spanning tussen wens en realiteit
blootlegt (geen oplossingen!).

The Deep Analysis (Tripartite): Minimaal 450 woorden, verdeeld over exact 3 paragrafen.
Elke paragraaf moet een diepgravende synthese zijn van één van de drie bronnen.
De bronnen moeten het spanningsveld verdiepen, niet louter feiten bevestigen.
Synthese boven samenvatting: de tekst gaat over het onderwerp en het conflict, niet
over de boeken zelf. Gebruik inzichten uit de bronnen om een eigen argumentatie op te bouwen.
Verboden zinnen: start een paragraaf nooit met "In dit boek...", "De auteur zegt..."
of "Dit hoofdstuk bespreekt...".
Integratie: verweef de bronnen organisch; de lezer volgt een filosofisch betoog waarin
bronnen als autoritaire wegwijzers functioneren.
Flow: zorg voor vloeiende overgangen tussen de drie paragrafen zodat het één doorlopend
essay van 450 woorden vormt.
Gouden Regels:
- Anonieme Autoriteit: noem de namen van de boeken of de auteurs nooit in de lopende tekst
  van de Deep Analysis; de lezer mag niet merken welk boek je gebruikt.
- Geïntegreerd Betoog: schrijf vanuit je eigen autoriteit over het onderwerp en het conflict.
  Gebruik wijsheid uit bronnen als algemene inzichten, niet als expliciete referenties.
- Focus op het Conflict: elke paragraaf onderzoekt een aspect van de spanning, zonder bronnen
  te benoemen.
- De Bronvermelding: de enige plek waar namen staan, is in de lijst "Bronnen" onderaan.
- Toon: poëtisch, observationeel en tijdloos; niet academisch of recenserend.

The Resolution: Eindig elk patroon met een normatieve formulering die start met:
"Therefore, ...". Dit is een houdingsgebod, geen instrumentele strategie.

Bronnen: Exact 3 boeken of papers (Auteur — Titel).

Kwaliteitsregel: Elk patroon moet aantoonbaar gedragen worden door zijn bronnen.
Geen vrijblijvende slotzinnen. Bij twijfel: dieper, scherper, filosofischer.
"""

load_dotenv()
ENV_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN", "").strip()


def get_client(api_key: str):
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK ontbreekt. Installeer de openai package.")
    return OpenAI(api_key=api_key)


def call_openai_json(client, messages, temperature=0.4):
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    raw_content = response.choices[0].message.content
    st.session_state.last_raw_ai_output = raw_content
    return json.loads(raw_content)


def generate_index(client, topic: str):
    messages = [
        {"role": "system", "content": V4_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Voer een onderwerp-scan uit en maak een index van precies 20 patronen.\n"
                "Output als JSON met deze velden:\n"
                "{"
                '"subject_scan": "...", '
                '"index": ['
                '{"number": 1, "title": "...", "scale": "Macro|Meso|Micro", "description": "..."}'
                "]}\n"
                f"Onderwerp: {topic}"
            ),
        },
    ]
    data = call_openai_json(client, messages, temperature=0.3)
    index = data.get("index", [])
    if len(index) != 20:
        raise ValueError("Index is niet precies 20 patronen.")
    return data


def generate_batch(client, topic: str, index_entries, batch_numbers, retry_note=None):
    batch_list = [p for p in index_entries if p["number"] in batch_numbers]
    retry_suffix = ""
    if retry_note:
        retry_suffix = f"\n{retry_note}"
    messages = [
        {"role": "system", "content": V4_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Schrijf de volledige patronen voor de volgende indexitems.\n"
                "Volg strikt de Strikte Anatomie per Patroon uit de system prompt.\n"
                "Verweef de bronnen inhoudelijk in de analyse (geen losse bronvermelding).\n"
                "BELANGRIJK: Scheid de 3 paragrafen van de Deep Analysis ALTIJD met een lege regel, "
                "zodat ze technisch herkenbaar zijn als 3 blokken.\n"
                "WEES ZEER UITGEBREID. Elke paragraaf moet minimaal 150 woorden bevatten. "
                "Als je te kort schrijft, faalt het systeem. Analyseer de bronnen diepgaand.\n"
                "Output als JSON met dit schema:\n"
                "{"
                '"patterns": ['
                '{"number": 1, "title": "...", "scale": "Macro|Meso|Micro", '
                '"conflict": "**...**", '
                '"paragraphs": ["...", "...", "..."], '
                '"resolution": "Therefore, ...", '
                '"sources": ["Auteur — Titel", "Auteur — Titel", "Auteur — Titel"]'
                "}"
                "]}\n"
                f"Onderwerp: {topic}\n"
                f"Indexitems: {json.dumps(batch_list, ensure_ascii=False)}"
                f"{retry_suffix}"
            ),
        },
    ]
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=0.5,
        response_format={"type": "json_object"},
    )
    raw_content = response.choices[0].message.content
    st.session_state.last_raw_ai_output = raw_content
    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError:
        print("RAW AI OUTPUT (JSON decode failed):\n", raw_content)
        data = {}
    patterns = data.get("patterns", [])
    if not isinstance(patterns, list) or not patterns:
        fallback = extract_patterns_from_text(raw_content)
        if fallback:
            patterns = fallback
        else:
            print("RAW AI OUTPUT (no patterns parsed):\n", raw_content)
    return patterns


def generate_front_matter(client, topic: str, index_entries):
    messages = [
        {"role": "system", "content": V4_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Genereer een voorwoord, drie leesinstructies en een nawoord.\n"
                "Gebruik uitsluitend 'long read' tekstblokken, geen lijstjes of bullets.\n"
                "Output als JSON met velden: "
                '{"foreword": "...", "reading_instructions": ["...", "...", "..."], "afterword": "..."}\n'
                f"Onderwerp: {topic}\n"
                f"Index: {json.dumps(index_entries, ensure_ascii=False)}"
            ),
        },
    ]
    return call_openai_json(client, messages, temperature=0.4)


def assemble_markdown(topic, index_data, patterns, front_matter):
    lines = []
    lines.append(f"# {topic}")
    lines.append("")
    lines.append(front_matter["foreword"])
    lines.append("")
    lines.append("## Leesinstructies")
    for i, text in enumerate(front_matter["reading_instructions"], start=1):
        lines.append(f"Leesinstructie {i}: {text}")
        lines.append("")
    lines.append("## Index van patronen")
    for item in index_data["index"]:
        lines.append(
            f"{item['number']}. {item['title']} "
            f"({item['scale']}) — {item['description']}"
        )
        lines.append("")
    lines.append("## Patronen")
    for number in range(1, 21):
        pattern = patterns[number]
        lines.append(f"## {pattern['number']}. {pattern['title']} ({pattern['scale']})")
        lines.append("")
        lines.append(pattern["conflict"].strip())
        lines.append("")
        for paragraph in pattern["paragraphs"]:
            lines.append(paragraph.strip())
            lines.append("")
        lines.append(pattern["resolution"].strip())
        lines.append("")
        sources_text = "; ".join(pattern["sources"])
        lines.append(f"Bronnen: {sources_text}")
        lines.append("")
    lines.append("## Nawoord")
    lines.append(front_matter["afterword"])
    lines.append("")
    return "\n".join(lines)


def assemble_markdown_from_patterns(topic, patterns):
    lines = []
    lines.append(f"# {topic}")
    lines.append("")
    lines.append("## Patronen")
    for number in sorted(patterns.keys()):
        pattern = patterns[number]
        lines.append(
            f"## {pattern.get('number', '?')}. {pattern.get('title', 'Niet gegenereerd')} "
            f"({pattern.get('scale', '')})"
        )
        lines.append("")
        lines.append(pattern.get("conflict", "Niet gegenereerd").strip())
        lines.append("")
        for paragraph in extract_paragraphs(pattern.get("paragraphs", [])):
            lines.append(paragraph.strip())
            lines.append("")
        lines.append(pattern.get("resolution", "Niet gegenereerd").strip())
        lines.append("")
        sources_text = "; ".join(pattern.get("sources") or [])
        lines.append(f"Bronnen: {sources_text if sources_text else 'Niet gegenereerd'}")
        lines.append("")
    return "\n".join(lines)


def extract_paragraphs(paragraphs_value):
    if isinstance(paragraphs_value, list):
        return [p for p in (p.strip() for p in paragraphs_value) if p]
    if isinstance(paragraphs_value, str):
        return [p for p in (p.strip() for p in paragraphs_value.split("\n\n")) if p]
    return []


def extract_patterns_from_text(raw_text):
    if not raw_text:
        return []
    heading_re = re.compile(
        r"^###\s*(\d+)\.\s*(.+?)(?:\s*\((Macro|Meso|Micro)\))?\s*$",
        re.MULTILINE,
    )
    matches = list(heading_re.finditer(raw_text))
    patterns = []
    for idx, match in enumerate(matches):
        number = int(match.group(1))
        title = match.group(2).strip()
        scale = match.group(3) or ""
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_text)
        block = raw_text[start:end].strip()
        conflict_match = re.search(r"(\*\*.+?\*\*)", block, re.DOTALL)
        conflict = conflict_match.group(1).strip() if conflict_match else ""
        resolution_match = re.search(
            r"(?:^|\n)\s*(?:#\s*)?Resolution[:\s]*([^\n]+)|"
            r"(?:^|\n)\s*(Therefore,[^\n]+)",
            block,
            re.IGNORECASE,
        )
        if resolution_match:
            resolution = (resolution_match.group(1) or resolution_match.group(2) or "").strip()
        else:
            resolution = ""
        sources_match = re.search(r"Bronnen:\s*(.+)", block)
        sources = []
        if sources_match:
            sources = [s.strip() for s in sources_match.group(1).split(";") if s.strip()]
        block_wo_sources = re.sub(r"Bronnen:\s*.+", "", block).strip()
        paragraphs = extract_paragraphs(block_wo_sources)
        patterns.append(
            {
                "number": number,
                "title": title,
                "scale": scale,
                "conflict": conflict,
                "paragraphs": paragraphs,
                "resolution": resolution,
                "sources": sources,
            }
        )
    return patterns


def validate_pattern(pattern):
    title = pattern.get("title", "").strip()
    if not title or ":" in title:
        raise ValueError("Titel moet beeldend zijn en geen dubbele punt bevatten.")
    conflict = pattern.get("conflict", "").strip()
    if not (conflict.startswith("**") and conflict.endswith("**")):
        raise ValueError("The Conflict moet vetgedrukt zijn en één probleemstelling bevatten.")
    paragraphs = extract_paragraphs(pattern.get("paragraphs", []))
    if len(paragraphs) != 3:
        raise ValueError("The Deep Analysis moet exact 3 paragrafen bevatten.")
    total_words = sum(len(p.split()) for p in paragraphs)
    if total_words < 350:
        raise ValueError("The Deep Analysis moet minimaal 350 woorden bevatten.")
    resolution = pattern.get("resolution", "").strip()
    if not resolution.startswith("Therefore,"):
        raise ValueError('The Resolution moet starten met "Therefore,".')
    sources = pattern.get("sources", [])
    if len(sources) != 3:
        raise ValueError("Er moeten exact 3 bronnen zijn.")
    for source in sources:
        if "—" not in source:
            raise ValueError("Bronnen moeten het formaat 'Auteur — Titel' volgen.")


def warn_book_review_style(paragraphs, pattern_number):
    forbidden_starts = ("In dit boek", "De auteur zegt", "Dit hoofdstuk bespreekt")
    for paragraph in paragraphs:
        text = paragraph.strip()
        if text.startswith(forbidden_starts):
            st.warning(
                f"Waarschuwing patroon {pattern_number}: paragraaf start als boekverslag. "
                "Herformuleer richting synthese."
            )


def markdown_to_pdf_bytes(markdown_text, title):
    if FPDF is None:
        raise RuntimeError("fpdf2 ontbreekt. Installeer fpdf2 voor PDF-export.")
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_title(title)
    font_name = "Helvetica"
    pdf.set_font(font_name, size=12)

    def sanitize_text(text):
        return normalize_pdf_text(text)

    def write_heading(text, level):
        sizes = {1: 18, 2: 15, 3: 13}
        pdf.set_font(font_name, style="B", size=sizes.get(level, 12))
        pdf.multi_cell(0, 8, sanitize_text(text))
        pdf.ln(2)
        pdf.set_font(font_name, size=12)

    for raw_line in markdown_text.splitlines():
        line = sanitize_text(raw_line.strip())
        if not line:
            pdf.ln(4)
            continue
        if line.startswith("### "):
            write_heading(line[4:], 3)
            continue
        if line.startswith("## "):
            write_heading(line[3:], 2)
            continue
        if line.startswith("# "):
            write_heading(line[2:], 1)
            continue
        pdf.multi_cell(0, 6, line)
        pdf.ln(1)

    return bytes(pdf.output(dest="S"))


def build_pdf_from_patterns(title, patterns):
    if FPDF is None:
        raise RuntimeError("fpdf2 ontbreekt. Installeer fpdf2 voor PDF-export.")

    patterns_sorted = sorted(patterns, key=lambda p: p.get("number", 0))

    def sanitize_text(text):
        return normalize_pdf_text(text)

    def render_patterns(pdf, capture_pages=False, font_name_override=None):
        toc_entries = []
        for pattern in patterns_sorted:
            start_page = pdf.page_no()
            if capture_pages:
                toc_entries.append(
                    (pattern.get("number", "?"), pattern.get("title", "Onbekend"), start_page)
                )
            heading_font = font_name_override or "Helvetica"
            pdf.set_font(heading_font, style="B", size=14)
            pdf.multi_cell(
                0,
                8,
                sanitize_text(
                    f"{pattern.get('number', '?')}. {pattern.get('title', '')} "
                    f"({pattern.get('scale', '')})"
                ),
            )
            pdf.ln(1)
            pdf.set_font(heading_font, size=12)
            conflict = sanitize_text(pattern.get("conflict", "").strip())
            if conflict:
                pdf.multi_cell(0, 6, conflict)
                pdf.ln(1)
            paragraphs = extract_paragraphs(pattern.get("paragraphs", []))
            for paragraph in paragraphs:
                pdf.multi_cell(0, 6, sanitize_text(paragraph))
                pdf.ln(1)
            resolution = sanitize_text(pattern.get("resolution", "").strip())
            if resolution:
                pdf.multi_cell(0, 6, resolution)
                pdf.ln(1)
            sources = pattern.get("sources", [])
            if sources:
                pdf.set_font(heading_font, style="I", size=11)
                pdf.multi_cell(0, 6, sanitize_text(f"Bronnen: {'; '.join(sources)}"))
                pdf.set_font(heading_font, size=12)
                pdf.ln(2)
        return toc_entries

    # First pass to capture page numbers
    first_pass = FPDF()
    first_pass.set_auto_page_break(auto=True, margin=18)
    first_pass.add_page()
    first_pass.set_title(title)
    font_name = "Helvetica"
    first_pass.set_font(font_name, size=12)
    toc_entries = render_patterns(first_pass, capture_pages=True, font_name_override=font_name)

    # Second pass with TOC on page 1
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_title(title)
    font_name = "Helvetica"
    pdf.set_font(font_name, style="B", size=18)
    pdf.multi_cell(0, 10, sanitize_text(title))
    pdf.ln(2)
    pdf.set_font(font_name, style="B", size=14)
    pdf.multi_cell(0, 8, "Inhoudsopgave")
    pdf.ln(2)
    pdf.set_font(font_name, size=12)
    for number, title_text, page_no in toc_entries:
        display_page = page_no + 1
        pdf.cell(
            0,
            6,
            sanitize_text(f"{number}. {title_text} .... {display_page}"),
            ln=1,
        )

    pdf.add_page()
    pdf.set_font(font_name, size=12)
    render_patterns(pdf, capture_pages=False, font_name_override=font_name)

    return bytes(pdf.output(dest="S"))


def convert_with_pandoc(markdown_text, title, output_basename, patterns=None, author=None):
    if pypandoc is None:
        raise RuntimeError("pypandoc ontbreekt. Installeer pandoc en pypandoc.")
    with tempfile.TemporaryDirectory() as tmpdir:
        md_path = os.path.join(tmpdir, f"{output_basename}.md")
        epub_path = os.path.join(tmpdir, f"{output_basename}.epub")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown_text)

        common_args = [
            "--toc",
            "--toc-depth=2",
            f'--metadata=title:{title}',
            "--top-level-division=chapter",
        ]
        if author:
            common_args.append(f"--metadata=author:{author}")

        if patterns:
            pdf_bytes = build_pdf_from_patterns(title, patterns)
        else:
            pdf_bytes = markdown_to_pdf_bytes(markdown_text, title)
        pypandoc.convert_file(
            md_path,
            "epub",
            outputfile=epub_path,
            extra_args=common_args + ["--epub-chapter-level=2"],
        )

        with open(epub_path, "rb") as f:
            epub_bytes = f.read()

    return pdf_bytes, epub_bytes


def upload_to_dropbox(file_content, file_name):
    if not DROPBOX_ACCESS_TOKEN:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN ontbreekt in .env.")
    dbx = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)
    path = f"/Kobo/MyBooks/{file_name}"
    dbx.files_upload(file_content, path, mode=dropbox.files.WriteMode("overwrite"))
    update_simple_index(dbx)
    return path


def update_simple_index(dbx):
    folder_path = "/Kobo/MyBooks"
    entries = dbx.files_list_folder(folder_path).entries
    files = [
        entry.name
        for entry in entries
        if hasattr(entry, "name")
        and entry.name.lower().endswith((".epub", ".pdf"))
        and entry.name != "index.html"
    ]
    files.sort()
    links = "\n".join(
        [f'<div><a href="{name}">{name}</a></div>' for name in files]
    )
    html = (
        "<!doctype html>"
        "<html><head><meta charset='utf-8'/>"
        "<title>Kobo Library</title>"
        "<style>body{font-family:Arial,Helvetica,sans-serif;"
        "font-size:32px;line-height:1.5;}a{display:block;padding:12px 0;}</style>"
        "</head><body>"
        "<h1>Kobo Library</h1>"
        f"{links}"
        "</body></html>"
    )
    dbx.files_upload(
        html.encode("utf-8"),
        f"{folder_path}/index.html",
        mode=dropbox.files.WriteMode("overwrite"),
    )


def normalize_pdf_text(text):
    cleaned = unidecode(text or "")
    cleaned = (
        cleaned.replace("—", "-")
        .replace("–", "-")
        .replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
        .replace("…", "...")
        .replace("•", "-")
    )
    for old, new in PDF_CHAR_REPLACEMENTS.items():
        cleaned = cleaned.replace(old, new)
    try:
        return cleaned.encode("latin-1").decode("latin-1")
    except UnicodeEncodeError:
        return cleaned.encode("latin-1", "replace").decode("latin-1")


def make_safe_filename(title, ext):
    base = (title or "pattern_language").strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^a-z0-9_]+", "", base)
    if not base:
        base = "pattern_language"
    return f"{base}.{ext}"


def init_state():
    st.session_state.setdefault("topic", "")
    st.session_state.setdefault("api_key", ENV_API_KEY or "")
    st.session_state.setdefault("author", "")
    st.session_state.setdefault("index_data", None)
    st.session_state.setdefault("patterns", {})
    st.session_state.setdefault("batch_status", {1: "pending", 2: "pending", 3: "pending", 4: "pending"})
    st.session_state.setdefault("front_matter", None)
    st.session_state.setdefault("markdown", None)
    st.session_state.setdefault("pdf_bytes", None)
    st.session_state.setdefault("epub_bytes", None)
    st.session_state.setdefault("last_error", "")
    st.session_state.setdefault("failed_batch_id", None)
    st.session_state.setdefault("retry_batch_id", None)
    st.session_state.setdefault("last_raw_ai_output", "")
    st.session_state.setdefault("final_pdf_bytes", None)


def reset_generation():
    st.session_state.index_data = None
    st.session_state.patterns = {}
    st.session_state.batch_status = {1: "pending", 2: "pending", 3: "pending", 4: "pending"}
    st.session_state.front_matter = None
    st.session_state.markdown = None
    st.session_state.pdf_bytes = None
    st.session_state.epub_bytes = None
    st.session_state.last_error = ""
    st.session_state.failed_batch_id = None
    st.session_state.retry_batch_id = None
    st.session_state.last_raw_ai_output = ""
    st.session_state.final_pdf_bytes = None
    st.session_state.epub_bytes = None


def batch_numbers(batch_id):
    if batch_id == 1:
        return list(range(1, 6))
    if batch_id == 2:
        return list(range(6, 11))
    if batch_id == 3:
        return list(range(11, 16))
    return list(range(16, 21))


def update_progress(progress_placeholder, caption_placeholder):
    completed = len(st.session_state.patterns)
    progress_placeholder.progress(completed / 20)
    caption_placeholder.caption(f"Voortgang: patroon {completed} van 20")


def store_pattern(pattern, log_container=None):
    if "number" not in pattern:
        if log_container is not None:
            log_container.error("Patroon mist 'number' en kan niet worden opgeslagen.")
        return
    pattern.setdefault("title", "Niet gegenereerd")
    pattern.setdefault("scale", "")
    pattern.setdefault("conflict", "Niet gegenereerd")
    pattern.setdefault("paragraphs", ["Niet gegenereerd", "Niet gegenereerd", "Niet gegenereerd"])
    pattern.setdefault("resolution", "Niet gegenereerd")
    pattern.setdefault("sources", [])
    patterns = dict(st.session_state.patterns)
    patterns[pattern["number"]] = pattern
    st.session_state.patterns = patterns
    if log_container is not None:
        log_container.info(
            f"Patroon {pattern['number']}: {pattern['title']} succesvol opgeslagen."
        )


def execute_batch(batch_id, client, index_entries, log_container, progress_placeholder, caption_placeholder):
    st.session_state.batch_status[batch_id] = "running"
    batch = generate_batch(
        client, st.session_state.topic, index_entries, batch_numbers(batch_id)
    )
    expected = len(batch_numbers(batch_id))
    if len(batch) < expected:
        st.warning(
            f"Batch {batch_id} leverde {len(batch)} patronen i.p.v. {expected}. "
            "Ik sla de beschikbare patronen op."
        )
    for pattern in batch:
        try:
            validate_pattern(pattern)
        except Exception as exc:
            pattern_number = pattern.get("number", "?")
            st.error(f"Fout bij patroon {pattern_number}: {exc}")
        store_pattern(pattern, log_container)
        warn_book_review_style(
            extract_paragraphs(pattern.get("paragraphs", [])),
            pattern.get("number", "?"),
        )
        update_progress(progress_placeholder, caption_placeholder)
    st.session_state.batch_status[batch_id] = "done"
    if batch_id == 1:
        st.success("Batch 1 succesvol gegenereerd!")


def main():
    st.set_page_config(page_title=APP_TITLE, layout="centered")
    init_state()

    st.title(APP_TITLE)
    st.write(f"Aantal patronen in geheugen: {len(st.session_state.patterns)}")
    st.write("Genereer een volledig Pattern Language boek in academisch Nederlands.")

    with st.container():
        st.subheader("Input")
        topic = st.text_input("Onderwerp", value=st.session_state.topic)
        author = st.text_input("Auteur (voor ePub)", value=st.session_state.author)
        api_key = st.text_input(
            "OpenAI API-sleutel",
            type="password",
            value=st.session_state.api_key,
            disabled=bool(ENV_API_KEY),
        )
        if ENV_API_KEY:
            st.caption("API-sleutel geladen uit .env (OPENAI_API_KEY).")
        col_a, col_b = st.columns([1, 1])
        with col_a:
            if st.button("Start nieuw project"):
                st.session_state.topic = topic
                st.session_state.author = author
                st.session_state.api_key = api_key
                reset_generation()
                st.rerun()
        with col_b:
            if st.button("Genereer index"):
                st.session_state.topic = topic
                st.session_state.author = author
                st.session_state.api_key = api_key
                try:
                    client = get_client(api_key)
                    st.session_state.index_data = generate_index(client, topic)
                    st.session_state.last_error = ""
                except Exception as exc:
                    st.session_state.last_error = str(exc)

    if st.session_state.last_error:
        st.error(st.session_state.last_error)
        if st.session_state.failed_batch_id:
            if st.button("Probeer Batch Opnieuw"):
                st.session_state.retry_batch_id = st.session_state.failed_batch_id
                st.session_state.last_error = ""
                st.rerun()

    if st.session_state.index_data:
        st.subheader("Index (20 patronen)")
        for item in st.session_state.index_data["index"]:
            st.write(f"{item['number']}. {item['title']} ({item['scale']}) — {item['description']}")

        st.subheader("Batch processing")
        progress_placeholder = st.empty()
        caption_placeholder = st.empty()
        log_container = st.container()
        update_progress(progress_placeholder, caption_placeholder)

        if st.session_state.retry_batch_id:
            try:
                client = get_client(st.session_state.api_key)
                index_entries = st.session_state.index_data["index"]
                batch_id = st.session_state.retry_batch_id
                st.session_state.retry_batch_id = None
                execute_batch(
                    batch_id, client, index_entries, log_container, progress_placeholder, caption_placeholder
                )
                st.session_state.last_error = ""
                st.session_state.failed_batch_id = None
            except Exception as exc:
                st.session_state.last_error = str(exc)
                st.session_state.failed_batch_id = batch_id

        if st.button("Genereer volledig boek"):
            batch_id = None
            try:
                client = get_client(st.session_state.api_key)
                index_entries = st.session_state.index_data["index"]
                for batch_id in range(1, 5):
                    if st.session_state.batch_status[batch_id] == "done":
                        continue
                    execute_batch(
                        batch_id, client, index_entries, log_container, progress_placeholder, caption_placeholder
                    )
                st.session_state.front_matter = generate_front_matter(
                    client, st.session_state.topic, index_entries
                )
                st.session_state.last_error = ""
                st.session_state.failed_batch_id = None
            except Exception as exc:
                st.session_state.last_error = str(exc)
                st.session_state.failed_batch_id = batch_id

        for batch_id in range(1, 5):
            status = st.session_state.batch_status[batch_id]
            label = f"Batch {batch_id} ({batch_numbers(batch_id)[0]}-{batch_numbers(batch_id)[-1]})"
            cols = st.columns([2, 1])
            with cols[0]:
                st.write(f"{label}: {status}")
            with cols[1]:
                if st.button(f"Genereer batch {batch_id}"):
                    try:
                        client = get_client(st.session_state.api_key)
                        index_entries = st.session_state.index_data["index"]
                        execute_batch(
                            batch_id, client, index_entries, log_container, progress_placeholder, caption_placeholder
                        )
                        st.session_state.last_error = ""
                        st.session_state.failed_batch_id = None
                    except Exception as exc:
                        st.session_state.last_error = str(exc)
                        st.session_state.failed_batch_id = batch_id

        if st.session_state.patterns:
            st.subheader("Gegenereerde Patronen")
            patterns_sorted = sorted(
                st.session_state.patterns.values(), key=lambda p: p["number"], reverse=True
            )
            for pattern in patterns_sorted:
                with st.container():
                    st.markdown(
                        f"### {pattern.get('number', '?')}. "
                        f"{pattern.get('title', 'Niet gegenereerd')} "
                        f"({pattern.get('scale', '')})"
                    )
                    st.markdown(pattern.get("conflict", "Niet gegenereerd"))
                    for paragraph in extract_paragraphs(pattern.get("paragraphs", [])):
                        st.markdown(paragraph)
                    st.markdown(pattern.get("resolution", "Resolutie niet gevonden"))
                    sources = pattern.get("sources") or []
                    st.markdown(f"Bronnen: {'; '.join(sources) if sources else 'Niet gegenereerd'}")
                    st.divider()

        with st.expander("Ruwe AI Output (debug)", expanded=False):
            st.text_area(
                "Ruwe AI Output",
                value=st.session_state.last_raw_ai_output or "",
                height=200,
            )

    if len(st.session_state.patterns) == 20 and st.session_state.front_matter:
        st.subheader("Conversie")
        if st.button("Maak PDF en ePub"):
            try:
                markdown_text = assemble_markdown(
                    st.session_state.topic,
                    st.session_state.index_data,
                    st.session_state.patterns,
                    st.session_state.front_matter,
                )
                st.session_state.markdown = markdown_text
                pdf_bytes, epub_bytes = convert_with_pandoc(
                    markdown_text,
                    st.session_state.topic,
                    f"pattern_language_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    patterns=list(st.session_state.patterns.values()),
                    author=st.session_state.author.strip() or None,
                )
                st.session_state.pdf_bytes = pdf_bytes
                st.session_state.epub_bytes = epub_bytes
                st.session_state.last_error = ""
                try:
                    pdf_name = make_safe_filename(st.session_state.topic, "pdf")
                    epub_name = make_safe_filename(st.session_state.topic, "epub")
                    pdf_path = upload_to_dropbox(pdf_bytes, pdf_name)
                    epub_path = upload_to_dropbox(epub_bytes, epub_name)
                    st.success("Bestand staat voor je klaar in Dropbox!")
                    st.info(f"Geüpload naar: {pdf_path}")
                    st.info(f"Geüpload naar: {epub_path}")
                except Exception as exc:
                    st.error(f"Dropbox upload mislukt: {exc}")
            except Exception as exc:
                st.session_state.last_error = str(exc)

    if st.session_state.patterns:
        st.subheader("Definitieve PDF")
        if st.button(
            "Create PDF",
            type="primary",
            use_container_width=True,
        ):
            try:
                st.session_state.final_pdf_bytes = build_pdf_from_patterns(
                    st.session_state.topic, list(st.session_state.patterns.values())
                )
                st.session_state.last_error = ""
            except Exception as exc:
                st.session_state.last_error = str(exc)

        st.subheader("ePub Export")
        if st.button("Genereer ePub", use_container_width=True):
            try:
                if st.session_state.front_matter and st.session_state.index_data:
                    markdown_text = assemble_markdown(
                        st.session_state.topic,
                        st.session_state.index_data,
                        st.session_state.patterns,
                        st.session_state.front_matter,
                    )
                else:
                    markdown_text = assemble_markdown_from_patterns(
                        st.session_state.topic,
                        st.session_state.patterns,
                    )
                st.session_state.markdown = markdown_text
                _, epub_bytes = convert_with_pandoc(
                    markdown_text,
                    st.session_state.topic,
                    f"pattern_language_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    patterns=list(st.session_state.patterns.values()),
                    author=st.session_state.author.strip() or None,
                )
                st.session_state.epub_bytes = epub_bytes
                st.session_state.last_error = ""
                try:
                    epub_name = make_safe_filename(st.session_state.topic, "epub")
                    epub_path = upload_to_dropbox(epub_bytes, epub_name)
                    st.success("Bestand staat voor je klaar in Dropbox!")
                    st.info(f"Geüpload naar: {epub_path}")
                except Exception as exc:
                    st.error(f"Dropbox upload mislukt: {exc}")
            except Exception as exc:
                st.session_state.last_error = str(exc)

    if (
        st.session_state.pdf_bytes
        or st.session_state.epub_bytes
        or st.session_state.final_pdf_bytes
    ):
        st.subheader("Export")
        pdf_name = make_safe_filename(st.session_state.topic, "pdf")
        epub_name = make_safe_filename(st.session_state.topic, "epub")
        final_pdf_name = make_safe_filename(f"{st.session_state.topic}_definitief", "pdf")
        if st.session_state.pdf_bytes:
            st.download_button(
                "Download PDF",
                data=st.session_state.pdf_bytes,
                file_name=pdf_name,
                mime="application/pdf",
                key="download_pdf_btn",
            )
        if st.session_state.epub_bytes:
            st.download_button(
                "Download ePub",
                data=st.session_state.epub_bytes,
                file_name=epub_name,
                mime="application/epub+zip",
                key="download_epub_btn",
            )
        if st.session_state.final_pdf_bytes:
            st.download_button(
                "Download Definitieve PDF",
                data=st.session_state.final_pdf_bytes,
                file_name=final_pdf_name,
                mime="application/pdf",
                key="download_final_pdf_btn",
            )
        if st.button("Verstuur naar mijn Kobo (Dropbox)"):
            try:
                if st.session_state.pdf_bytes:
                    pdf_path = upload_to_dropbox(st.session_state.pdf_bytes, pdf_name)
                    st.info(f"Geüpload naar: {pdf_path}")
                if st.session_state.epub_bytes:
                    epub_path = upload_to_dropbox(st.session_state.epub_bytes, epub_name)
                    st.info(f"Geüpload naar: {epub_path}")
                if st.session_state.final_pdf_bytes:
                    final_path = upload_to_dropbox(st.session_state.final_pdf_bytes, final_pdf_name)
                    st.info(f"Geüpload naar: {final_path}")
                st.success("Bestand staat voor je klaar in Dropbox!")
            except Exception as exc:
                st.error(f"Dropbox upload mislukt: {exc}")


if __name__ == "__main__":
    main()
