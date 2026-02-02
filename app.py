import json
import os
import re
import tempfile
from datetime import datetime

import streamlit as st
import dropbox
from unidecode import unidecode

from prompts import V4_SYSTEM_PROMPT
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


DROPBOX_APP_KEY = os.getenv("DROPBOX_APP_KEY", "").strip()
DROPBOX_APP_SECRET = os.getenv("DROPBOX_APP_SECRET", "").strip()
DROPBOX_REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN", "").strip()


def get_client():
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK ontbreekt. Installeer de openai package.")
    api_key = st.secrets.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ontbreekt in Streamlit Secrets.")
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
                "Genereer de 20 patronen als een strikte neerwaartse beweging (een waterval).\n"
                "Deel 1: De Context (Macro, ca. 6 patronen) – focus op grote systemen, "
                "structuren en het brede krachtenveld rond het onderwerp.\n"
                "Deel 2: De Interactie (Meso, ca. 8 patronen) – focus op plekken, interacties, "
                "processen en relaties in de directe omgeving van het onderwerp.\n"
                "Deel 3: De Essentie (Micro, ca. 6 patronen) – focus op zintuigen, details, "
                "materialiteit en intieme ervaring.\n"
                "Sorteer-regel: begin bij de meest abstracte/grote onderwerpen en eindig bij de "
                "meest concrete/kleine details.\n"
                "Laat de labels Macro/Meso/Micro niet zichtbaar zijn in de uiteindelijke titels.\n"
                "Titels moeten klinken als hoofdstukken uit 'A Pattern Language', maar mogen "
                "abstract en conceptueel zijn: denk aan grens, last, breuklijn, resonantie, "
                "mogelijkheid, geloofwaardigheid, tijdelijkheid.\n"
                "Gebruik een lexicon dat past bij visionaire kracht: vooruitzien, twijfel, sprong, "
                "draagkracht, weerstand, signaal, blindheid, vonk, innerlijke referentie, open einde.\n"
                "Vermijd kosmische of mythische metaforen (zoals kosmische dans, oeroude stromen, "
                "etherische nevels) tenzij het onderwerp daar letterlijk over gaat.\n"
                "Vermijd concrete fysieke objecten of locaties tenzij het onderwerp letterlijk "
                "over plekken of materie gaat.\n"
                "Geen standaard natuurmetaforen (water, bergen, stormen) tenzij het onderwerp "
                "letterlijk over natuur gaat.\n"
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


def generate_short_title(client, topic: str):
    messages = [
        {"role": "system", "content": V4_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Bedenk een korte, krachtige boektitel (maximaal 3 woorden).\n"
                'Output als JSON: {"title": "..."}\n'
                f"Onderwerp: {topic}"
            ),
        },
    ]
    data = call_openai_json(client, messages, temperature=0.4)
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("Korte titel ontbreekt in de AI-output.")
    return title


def generate_batch(client, topic: str, index_entries, batch_numbers, retry_note=None):
    batch_list = [p for p in index_entries if p["number"] in batch_numbers]
    total_patterns = 20
    retry_suffix = ""
    if retry_note:
        retry_suffix = f"\n{retry_note}"
    def phase_info(number):
        if number <= 7:
            return "Macro", "het grote geheel, context en systeem"
        if number <= 14:
            return "Meso", "interactie, tussenlaag en directe omgeving"
        return "Micro", "detail, textuur en intieme ervaring"

    per_pattern_instructions = []
    for item in batch_list:
        phase_label, phase_desc = phase_info(item["number"])
        per_pattern_instructions.append(
            (
                f"Schrijf nu Patroon {item['number']} van de {total_patterns}.\n\n"
                f"Onderwerp: {item['title']} Fase: {phase_label} (Focus op {phase_desc})\n\n"
                "Instructies voor deze run:\n\n"
                f"Zoek eerst 3 gezaghebbende bronnen die passen bij dit onderwerp en de huidige fase ({phase_label}).\n\n"
                "Schrijf de Deep Analysis: exact 3 paragrafen, minimaal 450 woorden totaal. "
                "Verwerk per paragraaf één bron.\n\n"
                "Hanteer de 'Anonieme Autoriteit': geen bronvermeldingen of auteursnamen in de tekst zelf.\n\n"
                "Vermijd alle verboden abstracties; wees zintuiglijk en fysiek.\n\n"
                "Eindig met de 'Therefore' resolutie en de lijst met 3 bronnen.\n\n"
                "Lever de output als valide JSON binnen de afgesproken velden.\n"
            )
        )
    messages = [
        {"role": "system", "content": V4_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Schrijf de volledige patronen voor de volgende indexitems.\n"
                "Volg de system prompt letterlijk en strikt.\n"
                "Verweef de bronnen inhoudelijk in de analyse (geen losse bronvermelding).\n"
                "Schrijf als een lens: geen uitleg, geen samenvatting, alleen gedachtebeweging.\n"
                "Zoek eerst 3 relevante boeken/titels bij dit specifieke onderwerp voordat je "
                "begint met schrijven.\n"
                "BELANGRIJK: Scheid de 3 paragrafen van de Deep Analysis ALTIJD met een lege regel, "
                "zodat ze technisch herkenbaar zijn als 3 blokken.\n"
                "WEES ZEER UITGEBREID. Elke paragraaf moet minimaal 150 woorden bevatten. "
                "Analyseer de bronnen diepgaand.\n"
                "Je krijgt per patroon het volgnummer en het totaal (20).\n"
                "Bepaal op basis van dit nummer of je je in de beginfase (Macro), middenfase (Meso) "
                "of eindfase (Micro) van het boek bevindt en pas je perspectief daarop aan.\n"
                "Gebruik deze indeling: 1-7 = Macro, 8-14 = Meso, 15-20 = Micro.\n"
                "\n"
                "Dynamische instructies per patroon:\n"
                f"{'\n---\n'.join(per_pattern_instructions)}\n"
                "Output als JSON met dit schema:\n"
                "{"
                '"patterns": ['
                '{"number": 1, "title": "...", "scale": "Macro|Meso|Micro", '
                '"conflict": "**...**", '
                '"analysis": "drie paragrafen met lege regels ertussen", '
                '"resolution": "Therefore, ...", '
                '"sources": ["Auteur — Titel", "Auteur — Titel", "Auteur — Titel"]'
                "}"
                "]}\n"
                "Lever je antwoord uitsluitend als een valide JSON-object. Zorg dat de tekst "
                "in de velden de gevraagde poëtische diepgang en academische strengheid heeft.\n"
                "De analysis is platte tekst: exact 3 paragrafen met lege regels ertussen. "
                "Geen LaTeX of technische opmaak. Laat analysis nooit leeg.\n"
                "Gebruik geen placeholders in sources (geen 'Auteur — Titel').\n"
                f"Schrijf specifiek over {topic}. Wees concreet, vermijd clichés en gebruik de "
                "drie bronnen voor je analyse. Als je de bronnen niet vermeldt onderaan, is de "
                "opdracht mislukt.\n"
                f"Onderwerp: {topic}\n"
                f"Totaal patronen: {total_patterns}\n"
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
    if retry_note is None and any(is_incomplete_pattern(p) for p in patterns):
        return generate_batch(
            client,
            topic,
            index_entries,
            batch_numbers,
            retry_note=(
                "De vorige output miste analysis-tekst of echte bronnen. "
                "Vul analysis met precies 3 paragrafen en geef 3 echte bronnen. "
                "Gebruik geen placeholders."
            ),
        )
    return patterns


def generate_front_matter(client, topic: str, index_entries):
    messages = [
        {"role": "system", "content": V4_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Genereer een voorwoord, drie leesinstructies en een nawoord.\n"
                "Schrijf als een lens: geen uitleg, geen advies, geen opsommingen.\n"
                "Voorwoord en nawoord: compact, filosofisch, zonder didactiek.\n"
                "Leesinstructies: korte, dwingende zinnen (geen bullets, geen nummering).\n"
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
            f"{item['number']}. {item['title']} — {item['description']}"
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
        for paragraph in extract_paragraphs(get_analysis_text(pattern)):
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


def get_analysis_text(pattern):
    if "analysis" in pattern and pattern.get("analysis"):
        return pattern.get("analysis", "")
    paragraphs = pattern.get("paragraphs", [])
    if isinstance(paragraphs, list):
        return "\n\n".join(paragraphs)
    return paragraphs or ""


def is_incomplete_pattern(pattern):
    analysis_text = get_analysis_text(pattern).strip()
    if not analysis_text:
        return True
    if len(extract_paragraphs(analysis_text)) < 3:
        return True
    sources = pattern.get("sources") or []
    if len(sources) != 3:
        return True
    if any("Auteur" in source and "Titel" in source for source in sources):
        return True
    return False


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
        analysis_text = block_wo_sources
        paragraphs = extract_paragraphs(analysis_text)
        patterns.append(
            {
                "number": number,
                "title": title,
                "scale": scale,
                "conflict": conflict,
                "analysis": analysis_text,
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
    analysis_text = get_analysis_text(pattern)
    paragraphs = extract_paragraphs(analysis_text)
    if len(paragraphs) != 3:
        raise ValueError("The Deep Analysis moet exact 3 paragrafen bevatten.")
    total_words = sum(len(p.split()) for p in paragraphs)
    if total_words < 250:
        raise ValueError("The Deep Analysis moet minimaal 250 woorden bevatten.")
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
            paragraphs = extract_paragraphs(get_analysis_text(pattern))
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

    first_pass = FPDF()
    first_pass.set_auto_page_break(auto=True, margin=18)
    first_pass.add_page()
    first_pass.set_title(title)
    font_name = "Helvetica"
    first_pass.set_font(font_name, size=12)
    toc_entries = render_patterns(first_pass, capture_pages=True, font_name_override=font_name)

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
    app_key = st.secrets.get("DROPBOX_APP_KEY") or os.getenv("DROPBOX_APP_KEY")
    app_secret = st.secrets.get("DROPBOX_APP_SECRET") or os.getenv("DROPBOX_APP_SECRET")
    refresh_token = st.secrets.get("DROPBOX_REFRESH_TOKEN") or os.getenv("DROPBOX_REFRESH_TOKEN")
    if not (app_key and app_secret and refresh_token):
        raise RuntimeError(
            "DROPBOX_APP_KEY, DROPBOX_APP_SECRET of DROPBOX_REFRESH_TOKEN ontbreekt."
        )
    dbx = dropbox.Dropbox(
        app_key=app_key,
        app_secret=app_secret,
        oauth2_refresh_token=refresh_token,
    )
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
    st.session_state.setdefault("author", "")
    st.session_state.setdefault("short_title", "")
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
    st.session_state.short_title = ""


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
    pattern.setdefault("analysis", "Niet gegenereerd")
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
    app_password = st.secrets.get("APP_PASSWORD", "").strip()
    entered_password = st.sidebar.text_input("Wachtwoord", type="password")
    if not app_password:
        st.error("APP_PASSWORD ontbreekt in Streamlit Secrets.")
        return
    if not entered_password:
        st.info("Voer het wachtwoord in om de AI-generator te ontgrendelen.")
        return
    if entered_password != app_password:
        st.error("Wachtwoord onjuist.")
        return
    st.write(f"Aantal patronen in geheugen: {len(st.session_state.patterns)}")
    st.write("Genereer een volledig Pattern Language boek in academisch Nederlands.")

    with st.container():
        st.subheader("Input")
        topic = st.text_input("Onderwerp", value=st.session_state.topic)
        author = st.text_input("Auteur (voor ePub)", value=st.session_state.author)
        col_a, col_b = st.columns([1, 1])
        with col_a:
            if st.button("Start nieuw project"):
                st.session_state.topic = topic
                st.session_state.author = author
                reset_generation()
                st.rerun()
        with col_b:
            if st.button("Genereer index"):
                st.session_state.topic = topic
                st.session_state.author = author
                try:
                    client = get_client()
                    st.session_state.index_data = generate_index(client, topic)
                    if not st.session_state.short_title:
                        st.session_state.short_title = generate_short_title(client, topic)
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
            st.write(f"{item['number']}. {item['title']} — {item['description']}")

        st.subheader("Batch processing")
        progress_placeholder = st.empty()
        caption_placeholder = st.empty()
        log_container = st.container()
        update_progress(progress_placeholder, caption_placeholder)

        if st.session_state.retry_batch_id:
            try:
                client = get_client()
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
                client = get_client()
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
                        client = get_client()
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
                book_title = st.session_state.short_title or st.session_state.topic
                markdown_text = assemble_markdown(
                    book_title,
                    st.session_state.index_data,
                    st.session_state.patterns,
                    st.session_state.front_matter,
                )
                st.session_state.markdown = markdown_text
                pdf_bytes, epub_bytes = convert_with_pandoc(
                    markdown_text,
                    book_title,
                    f"pattern_language_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    patterns=list(st.session_state.patterns.values()),
                    author=st.session_state.author.strip() or None,
                )
                st.session_state.pdf_bytes = pdf_bytes
                st.session_state.epub_bytes = epub_bytes
                st.session_state.last_error = ""
                try:
                    pdf_name = make_safe_filename(book_title, "pdf")
                    epub_name = make_safe_filename(book_title, "epub")
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
                book_title = st.session_state.short_title or st.session_state.topic
                st.session_state.final_pdf_bytes = build_pdf_from_patterns(
                    book_title, list(st.session_state.patterns.values())
                )
                st.session_state.last_error = ""
            except Exception as exc:
                st.session_state.last_error = str(exc)

        st.subheader("ePub Export")
        if st.button("Genereer ePub", use_container_width=True):
            try:
                book_title = st.session_state.short_title or st.session_state.topic
                if st.session_state.front_matter and st.session_state.index_data:
                    markdown_text = assemble_markdown(
                        book_title,
                        st.session_state.index_data,
                        st.session_state.patterns,
                        st.session_state.front_matter,
                    )
                else:
                    markdown_text = assemble_markdown_from_patterns(
                        book_title,
                        st.session_state.patterns,
                    )
                st.session_state.markdown = markdown_text
                _, epub_bytes = convert_with_pandoc(
                    markdown_text,
                    book_title,
                    f"pattern_language_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    patterns=list(st.session_state.patterns.values()),
                    author=st.session_state.author.strip() or None,
                )
                st.session_state.epub_bytes = epub_bytes
                st.session_state.last_error = ""
                try:
                    epub_name = make_safe_filename(book_title, "epub")
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
        book_title = st.session_state.short_title or st.session_state.topic
        pdf_name = make_safe_filename(book_title, "pdf")
        epub_name = make_safe_filename(book_title, "epub")
        final_pdf_name = make_safe_filename(f"{book_title}_definitief", "pdf")
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
