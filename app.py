import json
import os
import re
import tempfile
from datetime import datetime

import streamlit as st
import dropbox
from unidecode import unidecode

from prompts import V6_SYSTEM_PROMPT
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


def generate_index(client, topic: str, subject_scan=None, storyline=None):
    messages = [
        {"role": "system", "content": V6_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Voer een onderwerp-scan uit en maak een index van precies 20 patronen.\n"
                "Genereer een index van 20 patronen, geordend van abstract (Macro) naar concreet (Micro).\n"
                "Gebruik Macro/Meso/Micro labels in de JSON-output, maar laat deze labels "
                "niet zichtbaar zijn in de titels of beschrijvingen.\n"
                "Titels: krachtig en beeldend, zonder dubbele punten.\n"
                "Descriptions: uitgebreide samenvatting per patroon (2–3 zinnen) die de inhoud stuurt.\n"
                f"Gebruik deze geselecteerde spanningsassen als basis: "
                f"{json.dumps(subject_scan or [], ensure_ascii=False)}\n"
                f"Gebruik deze Macro/Meso/Micro verhaallijn als kader: "
                f"{json.dumps(storyline or {}, ensure_ascii=False)}\n"
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
    for item in index:
        description = (item.get("description") or "").strip()
        if not description or len(description.split()) < 10:
            raise ValueError("Index beschrijving te kort of ontbreekt.")
    return data


def generate_subject_scan(client, topic: str):
    messages = [
        {"role": "system", "content": V6_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Stap 0 — Onderwerp-scan: geef exact 10 scherpe, verrassende spanningsassen.\n"
                "Vermijd herhaling van het onderwerp en formuleer elke as als een spanning "
                "tussen twee krachten of perspectieven.\n"
                "Output als JSON met veld: {\"subject_scan\": [\"...\", \"...\"]}\n"
                f"Onderwerp: {topic}"
            ),
        },
    ]
    data = call_openai_json(client, messages, temperature=0.4)
    scan = data.get("subject_scan", [])
    if not isinstance(scan, list) or len(scan) != 10:
        raise ValueError("Onderwerp-scan moet exact 10 observaties bevatten.")
    return scan


def generate_storyline(client, topic: str, subject_scan):
    messages = [
        {"role": "system", "content": V6_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Stap 1 — Macro→Micro verhaallijn: schrijf drie duidelijke delen "
                "(Macro, Meso, Micro), elk 2–4 zinnen.\n"
                "Gebruik de spanningsassen als ruggengraat.\n"
                "Output als JSON met velden: "
                "{\"macro\": \"...\", \"meso\": \"...\", \"micro\": \"...\"}\n"
                f"Onderwerp: {topic}\n"
                f"Spanningsassen: {json.dumps(subject_scan or [], ensure_ascii=False)}"
            ),
        },
    ]
    data = call_openai_json(client, messages, temperature=0.4)
    macro = (data.get("macro") or "").strip()
    meso = (data.get("meso") or "").strip()
    micro = (data.get("micro") or "").strip()
    if not (macro and meso and micro):
        raise ValueError("Verhaallijn (macro/meso/micro) ontbreekt in de AI-output.")
    return {"macro": macro, "meso": meso, "micro": micro}


def generate_sources_for_index(client, topic: str, index_entries, storyline):
    messages = [
        {"role": "system", "content": V6_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Stap 3 — Bronnen: kies exact 3 gezaghebbende bronnen per patroon.\n"
                "Output als JSON met schema:\n"
                "{"
                '"sources": ['
                '{"number": 1, "sources": ["Auteur — Titel", "Auteur — Titel", "Auteur — Titel"]}'
                "]}\n"
                f"Onderwerp: {topic}\n"
                f"Verhaallijn: {json.dumps(storyline or {}, ensure_ascii=False)}\n"
                f"Index (titels + descriptions): {json.dumps(index_entries, ensure_ascii=False)}"
            ),
        },
    ]
    data = call_openai_json(client, messages, temperature=0.3)
    sources = data.get("sources", [])
    if not isinstance(sources, list) or len(sources) != 20:
        raise ValueError("Bronnenlijst moet 20 items bevatten.")
    return {item["number"]: item["sources"] for item in sources}


def generate_pattern_single(client, topic, index_item, sources, storyline, subject_scan):
    messages = [
        {"role": "system", "content": V6_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Schrijf één patroon volgens de V4-structuur.\n"
                "Gebruik exact de 3 gegeven bronnen en noem ze alleen in de lijst onderaan.\n"
                "Gebruik de description als inhoudelijke ruggengraat; werk die concreet uit.\n"
                "Gebruik uitsluitend het onderstaande pakket als inhoudelijke input.\n"
                "Begin de analysis met een compacte parafrase van de description (1–2 zinnen), "
                "en ga daarna direct de diepte in.\n"
                "Output als JSON met schema:\n"
                "{"
                '"pattern": {'
                '"number": 1, "title": "...", "scale": "Macro|Meso|Micro", '
                '"conflict": "**...**", '
                '"analysis": "drie paragrafen met lege regels ertussen", '
                '"resolution": "Therefore, ...", '
                '"sources": ["Auteur — Titel", "Auteur — Titel", "Auteur — Titel"]'
                "}"
                "}\n"
                f"Verhaallijn: {json.dumps(storyline or {}, ensure_ascii=False)}\n"
                f"Spanningsassen: {json.dumps(subject_scan or [], ensure_ascii=False)}\n"
                f"Indexitem (titel + description): {json.dumps(index_item, ensure_ascii=False)}\n"
                f"Bronnen (verplicht): {json.dumps(sources, ensure_ascii=False)}"
            ),
        },
    ]
    data = call_openai_json(client, messages, temperature=0.4)
    pattern = data.get("pattern")
    if not pattern and "patterns" in data and isinstance(data.get("patterns"), list):
        for item in data.get("patterns", []):
            if item.get("number") == index_item.get("number"):
                pattern = item
    if not pattern and isinstance(data, dict) and data.get("number") == index_item.get("number"):
        if all(key in data for key in ["title", "conflict", "analysis", "resolution", "sources"]):
            pattern = data
    if not pattern:
        raise ValueError("Patroon ontbreekt in de AI-output.")
    if pattern.get("number") != index_item.get("number"):
        pattern["number"] = index_item.get("number")
    if not (index_item.get("description") or "").strip():
        st.warning("Index description ontbreekt; patroon kan drift vertonen.")
    return pattern


def generate_short_title(client, topic: str):
    messages = [
        {"role": "system", "content": V6_SYSTEM_PROMPT},
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
    expected_count = len(batch_list)
    retry_suffix = ""
    if retry_note:
        retry_suffix = f"\n{retry_note}"
    def phase_info(number):
        if number <= 5:
            return "Macro", "filosofie, context en het grote plaatje"
        if number <= 10:
            return "Meso", "structuur, systeem en architectuur"
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
                "Schrijf de Deep Analysis: exact 3 paragrafen, minimaal 300 woorden totaal. "
                "Verwerk per paragraaf één bron.\n\n"
                "Hanteer de 'Anonieme Autoriteit': geen bronvermeldingen of auteursnamen in de tekst zelf.\n\n"
                "Vermijd alle verboden abstracties; wees zintuiglijk en fysiek.\n\n"
                "Eindig met de 'Therefore' resolutie en de lijst met 3 bronnen.\n\n"
                "Lever de output als valide JSON binnen de afgesproken velden.\n"
            )
        )
    messages = [
        {"role": "system", "content": V6_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Schrijf de volledige patronen voor de volgende indexitems.\n"
                "Volg de system prompt letterlijk en strikt.\n"
                "Verweef de bronnen inhoudelijk in de analyse (geen losse bronvermelding).\n"
                "Zoek eerst 3 relevante boeken/titels bij dit specifieke onderwerp voordat je "
                "begint met schrijven.\n"
                "BELANGRIJK: Scheid de 3 paragrafen van de Deep Analysis ALTIJD met een lege regel, "
                "zodat ze technisch herkenbaar zijn als 3 blokken.\n"
                "Schrijf compact en precies; analyseer de bronnen diepgaand.\n"
                "Je krijgt per patroon het volgnummer en het totaal (20).\n"
                "Bepaal op basis van dit nummer of je je in de beginfase (Macro), middenfase (Meso) "
                "of eindfase (Micro) van het boek bevindt en pas je perspectief daarop aan.\n"
                "Gebruik deze indeling: 1-5 = Macro, 6-10 = Meso, 11-20 = Micro.\n"
                "\n"
                "Dynamische instructies per patroon:\n"
                f"{'\n---\n'.join(per_pattern_instructions)}\n"
                f"Je MOET exact {expected_count} patronen teruggeven, één voor elk indexitem.\n"
                f"Indexitem nummers: {[item['number'] for item in batch_list]}\n"
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
                "Conflict moet vetgedrukt zijn en expliciet de spanning formuleren "
                "(X wil Y, maar Z maakt Y onmogelijk).\n"
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
    if retry_note is None and len(patterns) < expected_count:
        return generate_batch(
            client,
            topic,
            index_entries,
            batch_numbers,
            retry_note=(
                f"Je gaf {len(patterns)} patronen terug. "
                f"Lever nu exact {expected_count} patronen, één per indexitemnummer."
            ),
        )
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
        {"role": "system", "content": V6_SYSTEM_PROMPT},
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


def generate_foreword_from_pattern(client, topic: str, pattern):
    messages = [
        {"role": "system", "content": V6_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Schrijf een compact voorwoord (max 200-300 woorden) dat de gedachte achter het boek "
                "verwoordt en de context opent. Baseer dit uitsluitend op het eerste patroon hieronder. "
                "Geen opsommingen.\n"
                "Output als JSON met veld: {\"foreword\": \"...\"}\n"
                f"Onderwerp: {topic}\n"
                f"Eerste patroon: {json.dumps(pattern, ensure_ascii=False)}"
            ),
        },
    ]
    data = call_openai_json(client, messages, temperature=0.4)
    return (data.get("foreword") or "").strip()


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
        for paragraph in extract_paragraphs(get_analysis_text(pattern)):
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
    if total_words < 300:
        raise ValueError("The Deep Analysis moet minimaal 300 woorden bevatten.")
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
    pdf.set_margins(left=22, top=24, right=22)
    pdf.set_auto_page_break(auto=True, margin=24)
    pdf.add_page()
    pdf.set_title(title)
    font_name = "Helvetica"
    pdf.set_font(font_name, size=12)

    def sanitize_text(text):
        return normalize_pdf_text(text)

    def write_heading(text, level):
        sizes = {1: 18, 2: 15, 3: 13}
        pdf.set_font(font_name, style="B", size=sizes.get(level, 12))
        pdf.multi_cell(0, 9, sanitize_text(text))
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
        pdf.multi_cell(0, 7, line)
        pdf.ln(1)

    return bytes(pdf.output(dest="S"))


def build_pdf_from_patterns(title, patterns, foreword=None, tagline=None, index_data=None):
    if FPDF is None:
        raise RuntimeError("fpdf2 ontbreekt. Installeer fpdf2 voor PDF-export.")

    patterns_sorted = sorted(patterns, key=lambda p: p.get("number", 0))

    def sanitize_text(text):
        return normalize_pdf_text(text)

    def render_title_page(pdf, font_name_override=None):
        heading_font = font_name_override or "Helvetica"
        pdf.set_font(heading_font, style="B", size=20)
        pdf.multi_cell(0, 10, sanitize_text(title))
        pdf.ln(4)
        if tagline:
            pdf.set_font(heading_font, size=12)
            pdf.multi_cell(0, 7, sanitize_text(tagline))

    def render_index_page(pdf, font_name_override=None):
        if not index_data:
            return
        heading_font = font_name_override or "Helvetica"
        pdf.set_font(heading_font, style="B", size=16)
        pdf.multi_cell(0, 9, sanitize_text("Index"))
        pdf.ln(2)
        pdf.set_font(heading_font, size=12)
        for item in index_data.get("index", []):
            line = f"{item['number']}. {item['title']} — {item['description']}"
            pdf.multi_cell(0, 7, sanitize_text(line))
            pdf.ln(1)

    def render_foreword(pdf, font_name_override=None):
        if not foreword:
            return
        heading_font = font_name_override or "Helvetica"
        pdf.set_font(heading_font, style="B", size=16)
        pdf.multi_cell(0, 9, sanitize_text("Voorwoord"))
        pdf.ln(2)
        pdf.set_font(heading_font, size=12)
        for paragraph in extract_paragraphs(foreword):
            pdf.multi_cell(0, 7, sanitize_text(paragraph))
            pdf.ln(1)

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
                pdf.multi_cell(0, 7, conflict)
                pdf.ln(1)
            paragraphs = extract_paragraphs(get_analysis_text(pattern))
            for paragraph in paragraphs:
                pdf.multi_cell(0, 7, sanitize_text(paragraph))
                pdf.ln(1)
            resolution = sanitize_text(pattern.get("resolution", "").strip())
            if resolution:
                pdf.multi_cell(0, 7, resolution)
                pdf.ln(1)
            sources = pattern.get("sources", [])
            if sources:
                pdf.set_font(heading_font, style="I", size=11)
                pdf.multi_cell(0, 6, sanitize_text(f"Bronnen: {'; '.join(sources)}"))
                pdf.set_font(heading_font, size=12)
                pdf.ln(2)
        return toc_entries

    first_pass = FPDF()
    first_pass.set_margins(left=22, top=24, right=22)
    first_pass.set_auto_page_break(auto=True, margin=24)
    first_pass.add_page()
    first_pass.set_title(title)
    font_name = "Helvetica"
    first_pass.set_font(font_name, size=12)
    render_title_page(first_pass, font_name_override=font_name)
    first_pass.add_page()
    render_index_page(first_pass, font_name_override=font_name)
    first_pass.add_page()
    render_foreword(first_pass, font_name_override=font_name)
    if foreword:
        first_pass.add_page()
    toc_entries = render_patterns(first_pass, capture_pages=True, font_name_override=font_name)

    pdf = FPDF()
    pdf.set_margins(left=22, top=24, right=22)
    pdf.set_auto_page_break(auto=True, margin=24)
    pdf.add_page()
    pdf.set_title(title)
    font_name = "Helvetica"
    render_title_page(pdf, font_name_override=font_name)
    pdf.add_page()
    render_index_page(pdf, font_name_override=font_name)
    pdf.add_page()
    render_foreword(pdf, font_name_override=font_name)
    if foreword:
        pdf.add_page()
    render_patterns(pdf, capture_pages=False, font_name_override=font_name)

    return bytes(pdf.output(dest="S"))


def convert_with_pandoc(markdown_text, title, output_basename, patterns=None, author=None, foreword=None):
    if pypandoc is None:
        raise RuntimeError("pypandoc ontbreekt. Installeer pandoc en pypandoc.")
    with tempfile.TemporaryDirectory() as tmpdir:
        md_path = os.path.join(tmpdir, f"{output_basename}.md")
        epub_path = os.path.join(tmpdir, f"{output_basename}.epub")
        css_path = os.path.join(tmpdir, "epub.css")
        cover_path = os.path.join(tmpdir, "cover.svg")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown_text)
        with open(css_path, "w", encoding="utf-8") as f:
            f.write(
                "body { font-family: serif; font-size: 9pt; line-height: 1.2; margin: 1.2em; }\n"
                "h1, h2, h3 { font-family: sans-serif; }\n"
                "h1 { font-size: 1.6em; margin-top: 0.6em; }\n"
                "h2 { font-size: 1.3em; margin-top: 0.8em; }\n"
                "h3 { font-size: 1.1em; margin-top: 0.8em; }\n"
                "p { margin: 0 0 0.8em 0; }\n"
            )
        generate_epub_cover_svg(title, cover_path)

        common_args = [
            "--toc",
            "--toc-depth=2",
            f'--metadata=title:{title}',
            "--top-level-division=chapter",
        ]
        if author:
            common_args.append(f"--metadata=author:{author}")

        if patterns:
            pdf_bytes = build_pdf_from_patterns(
                title,
                patterns,
                foreword=foreword,
                tagline=f"Een patroonlandschap rond {title}",
                index_data=None,
            )
        else:
            pdf_bytes = markdown_to_pdf_bytes(markdown_text, title)
        pypandoc.convert_file(
            md_path,
            "epub",
            outputfile=epub_path,
            extra_args=common_args
            + ["--epub-chapter-level=2", f"--css={css_path}", f"--epub-cover-image={cover_path}"],
        )

        with open(epub_path, "rb") as f:
            epub_bytes = f.read()

    return pdf_bytes, epub_bytes


def generate_epub_cover_svg(title, output_path):
    safe_title = (title or "").strip()
    bg_colors = ["#1f2937", "#374151", "#1e3a8a", "#334155", "#0f172a"]
    color_index = sum(ord(c) for c in safe_title) % len(bg_colors)
    bg = bg_colors[color_index]
    subtitle = "A Pattern Language"
    svg = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<svg xmlns='http://www.w3.org/2000/svg' width='1600' height='2560' viewBox='0 0 1600 2560'>"
        f"<rect width='1600' height='2560' fill='{bg}'/>"
        "<rect x='140' y='320' width='1320' height='1920' fill='none' stroke='#ffffff' stroke-width='4'/>"
        f"<text x='800' y='1180' text-anchor='middle' font-family='Helvetica, Arial, sans-serif' "
        "font-size='110' fill='#ffffff'>"
        f"{escape_xml_text(safe_title)}</text>"
        f"<text x='800' y='1320' text-anchor='middle' font-family='Helvetica, Arial, sans-serif' "
        "font-size='52' fill='#e5e7eb'>"
        f"{escape_xml_text(subtitle)}</text>"
        "</svg>"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(svg)


def escape_xml_text(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def upload_to_dropbox(file_content, file_name):
    if not (
        st.secrets.get("DROPBOX_REFRESH_TOKEN")
        and st.secrets.get("DROPBOX_APP_KEY")
        and st.secrets.get("DROPBOX_APP_SECRET")
    ):
        raise RuntimeError(
            "DROPBOX_APP_KEY, DROPBOX_APP_SECRET of DROPBOX_REFRESH_TOKEN ontbreekt."
        )
    dbx = dropbox.Dropbox(
        oauth2_refresh_token=st.secrets["DROPBOX_REFRESH_TOKEN"],
        app_key=st.secrets["DROPBOX_APP_KEY"],
        app_secret=st.secrets["DROPBOX_APP_SECRET"],
    )
    folder_path = "/Apps/Rakuten Kobo"
    try:
        dbx.files_create_folder_v2(folder_path)
    except Exception:
        pass
    path = f"{folder_path}/{file_name}"
    dbx.files_upload(file_content, path, mode=dropbox.files.WriteMode("overwrite"))
    try:
        update_simple_index(dbx, folder_path)
    except Exception:
        pass
    return path


def update_simple_index(dbx, folder_path="/Apps/Rakuten Kobo"):
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
    st.session_state.setdefault("subject_scan", [])
    st.session_state.setdefault("subject_scan_approved", False)
    st.session_state.setdefault("subject_scan_selected", [])
    st.session_state.setdefault("storyline", {})
    st.session_state.setdefault("storyline_approved", False)
    st.session_state.setdefault("sources_by_number", {})
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
    st.session_state.subject_scan = []
    st.session_state.subject_scan_approved = False
    st.session_state.subject_scan_selected = []
    st.session_state.storyline = {}
    st.session_state.storyline_approved = False
    st.session_state.sources_by_number = {}


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
            if "minimaal 300 woorden" in str(exc):
                st.warning(f"Patroon {pattern_number} is korter dan gewenst: {exc}")
            else:
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
            if st.button("Genereer onderwerp-scan"):
                st.session_state.topic = topic
                st.session_state.author = author
                try:
                    client = get_client()
                    st.session_state.subject_scan = generate_subject_scan(client, topic)
                    st.session_state.subject_scan_approved = False
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

    if st.session_state.subject_scan:
        st.subheader("Onderwerp-scan (kies 5–8 spanningsassen)")
        selected = []
        for i, item in enumerate(st.session_state.subject_scan):
            if st.checkbox(item, key=f"scan_{i}"):
                selected.append(item)
        st.session_state.subject_scan_selected = selected
        selected_count = len(st.session_state.subject_scan_selected)
        st.caption(f"Geselecteerd: {selected_count} (kies 5–8)")
        if st.button("Genereer verhaallijn"):
            if 5 <= selected_count <= 8:
                try:
                    client = get_client()
                    st.session_state.subject_scan_approved = True
                    st.session_state.storyline = generate_storyline(
                        client,
                        st.session_state.topic,
                        st.session_state.subject_scan_selected,
                    )
                    st.session_state.storyline_approved = False
                    st.session_state.last_error = ""
                except Exception as exc:
                    st.session_state.last_error = str(exc)
            else:
                st.session_state.last_error = "Selecteer 5–8 spanningsassen."

    if st.session_state.storyline:
        st.subheader("Verhaallijn (Macro → Micro)")
        st.write(f"Macro: {st.session_state.storyline.get('macro', '')}")
        st.write(f"Meso: {st.session_state.storyline.get('meso', '')}")
        st.write(f"Micro: {st.session_state.storyline.get('micro', '')}")
        if st.button("Goedkeuren verhaallijn"):
            st.session_state.storyline_approved = True

    if st.session_state.storyline_approved:
        if st.button("Genereer index"):
            try:
                client = get_client()
                st.session_state.index_data = generate_index(
                    client,
                    st.session_state.topic,
                    st.session_state.subject_scan_selected,
                    st.session_state.storyline,
                )
                st.session_state.last_error = ""
            except Exception as exc:
                st.session_state.last_error = str(exc)

    if st.session_state.index_data:
        st.subheader("Index (20 patronen)")
        for item in st.session_state.index_data["index"]:
            st.write(f"{item['number']}. {item['title']} — {item['description']}")
        if st.button("Genereer bronnen per patroon"):
            try:
                client = get_client()
                st.session_state.sources_by_number = generate_sources_for_index(
                    client,
                    st.session_state.topic,
                    st.session_state.index_data["index"],
                    st.session_state.storyline,
                )
                st.session_state.last_error = ""
            except Exception as exc:
                st.session_state.last_error = str(exc)

        if st.session_state.sources_by_number:
            progress_placeholder = st.empty()
            caption_placeholder = st.empty()
            log_container = st.container()
            update_progress(progress_placeholder, caption_placeholder)

            st.subheader("Patronen (per hoofdstuk)")

            if st.button("Genereer alle patronen (1 voor 1)"):
                try:
                    client = get_client()
                    for item in st.session_state.index_data["index"]:
                        number = item["number"]
                        if number in st.session_state.patterns:
                            continue
                        try:
                            pattern = generate_pattern_single(
                                client,
                                st.session_state.topic,
                                item,
                                st.session_state.sources_by_number.get(number, []),
                                st.session_state.storyline,
                                st.session_state.subject_scan_selected,
                            )
                            st.session_state.last_raw_ai_output = json.dumps(pattern, ensure_ascii=False, indent=2)
                            if pattern.get("number") != number:
                                st.warning(
                                    f"Patroon {number} kreeg nummer {pattern.get('number')} van de AI; gecorrigeerd."
                                )
                            try:
                                validate_pattern(pattern)
                            except Exception as exc:
                                st.warning(f"Patroon {number} validatie: {exc}")
                            store_pattern(pattern, log_container)
                            update_progress(progress_placeholder, caption_placeholder)
                        except Exception as exc:
                            st.error(f"Patroon {number} mislukt: {exc}")
                    st.session_state.last_error = ""
                except Exception as exc:
                    st.session_state.last_error = str(exc)

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
                        analysis_text = get_analysis_text(pattern)
                        paragraphs = extract_paragraphs(analysis_text)
                        if paragraphs:
                            for paragraph in paragraphs:
                                st.markdown(paragraph)
                        else:
                            st.error("Analysis ontbreekt in de AI-output.")
                        st.markdown(pattern.get("resolution", "Resolutie niet gevonden"))
                        sources = pattern.get("sources") or []
                        st.markdown(f"Bronnen: {'; '.join(sources) if sources else 'Niet gegenereerd'}")
                        st.divider()

        if st.session_state.sources_by_number:
            st.subheader("Pakketten per patroon")
            for item in st.session_state.index_data["index"]:
                number = item["number"]
                sources = st.session_state.sources_by_number.get(number, [])
                if sources:
                    with st.container():
                        st.markdown(f"**{item['title']} — {item['description']}**")
                        st.markdown(f"{'; '.join(sources)}")
                        if st.button(f"Genereer patroon {number}", key=f"gen_pkg_{number}"):
                            try:
                                client = get_client()
                                pattern = generate_pattern_single(
                                    client,
                                    st.session_state.topic,
                                    item,
                                    sources,
                                    st.session_state.storyline,
                                    st.session_state.subject_scan_selected,
                                )
                                st.session_state.last_raw_ai_output = json.dumps(
                                    pattern, ensure_ascii=False, indent=2
                                )
                                if pattern.get("number") != number:
                                    st.warning(
                                        f"Patroon {number} kreeg nummer {pattern.get('number')} van de AI; gecorrigeerd."
                                    )
                                try:
                                    validate_pattern(pattern)
                                except Exception as exc:
                                    st.warning(f"Patroon {number} validatie: {exc}")
                                store_pattern(pattern, log_container)
                                update_progress(progress_placeholder, caption_placeholder)
                                st.session_state.last_error = ""
                            except Exception as exc:
                                st.session_state.last_error = str(exc)
                        pattern = st.session_state.patterns.get(number)
                        if pattern:
                            st.markdown(
                                f"### {pattern.get('number', '?')}. "
                                f"{pattern.get('title', 'Niet gegenereerd')} "
                                f"({pattern.get('scale', '')})"
                            )
                            st.markdown(pattern.get("conflict", "Niet gegenereerd"))
                            analysis_text = get_analysis_text(pattern)
                            paragraphs = extract_paragraphs(analysis_text)
                            if paragraphs:
                                for paragraph in paragraphs:
                                    st.markdown(paragraph)
                            else:
                                st.error("Analysis ontbreekt in de AI-output.")
                            st.markdown(pattern.get("resolution", "Resolutie niet gevonden"))
                            sources = pattern.get("sources") or []
                            st.markdown(
                                f"Bronnen: {'; '.join(sources) if sources else 'Niet gegenereerd'}"
                            )
                    st.divider()

    if st.session_state.patterns and not st.session_state.sources_by_number:
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
                analysis_text = get_analysis_text(pattern)
                paragraphs = extract_paragraphs(analysis_text)
                if paragraphs:
                    for paragraph in paragraphs:
                        st.markdown(paragraph)
                else:
                    st.error("Analysis ontbreekt in de AI-output.")
                st.markdown(pattern.get("resolution", "Resolutie niet gevonden"))
                sources = pattern.get("sources") or []
                st.markdown(f"Bronnen: {'; '.join(sources) if sources else 'Niet gegenereerd'}")
                st.divider()

        # Weergave van gegenereerde patronen staat nu boven de pakketten

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
                tagline = f"Een patroonlandschap rond {book_title}"
                pdf_bytes, epub_bytes = convert_with_pandoc(
                    markdown_text,
                    book_title,
                    f"pattern_language_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    patterns=list(st.session_state.patterns.values()),
                    author=st.session_state.author.strip() or None,
                    foreword=st.session_state.front_matter.get("foreword")
                    if st.session_state.front_matter
                    else None,
                )
                st.session_state.pdf_bytes = pdf_bytes
                st.session_state.epub_bytes = epub_bytes
                st.session_state.last_error = ""
                try:
                    pdf_name = make_safe_filename(book_title, "pdf")
                    epub_name = make_safe_filename(f"{book_title}.kepub", "epub")
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
                tagline = f"Een patroonlandschap rond {book_title}"
                st.session_state.final_pdf_bytes = build_pdf_from_patterns(
                    book_title,
                    list(st.session_state.patterns.values()),
                    foreword=st.session_state.front_matter.get("foreword")
                    if st.session_state.front_matter
                    else None,
                    tagline=tagline,
                    index_data=st.session_state.index_data,
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
                    epub_name = make_safe_filename(f"{book_title}.kepub", "epub")
                    epub_path = upload_to_dropbox(epub_bytes, epub_name)
                    st.success("Bestand staat voor je klaar in Dropbox!")
                    st.info(f"Geüpload naar: {epub_path}")
                except Exception as exc:
                    st.error(f"Dropbox upload mislukt: {exc}")
            except Exception as exc:
                st.session_state.last_error = str(exc)

        st.subheader("Voorwoord")
        if st.button("Genereer voorwoord op basis van patroon 1", use_container_width=True):
            try:
                client = get_client()
                pattern_1 = st.session_state.patterns.get(1)
                if not pattern_1:
                    raise RuntimeError("Patroon 1 ontbreekt.")
                foreword = generate_foreword_from_pattern(client, st.session_state.topic, pattern_1)
                if not foreword:
                    raise RuntimeError("Voorwoord ontbreekt in de AI-output.")
                if not st.session_state.front_matter:
                    st.session_state.front_matter = {
                        "foreword": foreword,
                        "reading_instructions": ["", "", ""],
                        "afterword": "",
                    }
                else:
                    st.session_state.front_matter["foreword"] = foreword
                st.success("Voorwoord bijgewerkt.")
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
        epub_name = make_safe_filename(f"{book_title}.kepub", "epub")
        final_pdf_name = make_safe_filename(f"{book_title}_definitief", "pdf")
        if st.button("Genereer ePub (test)"):
            try:
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
                    foreword=st.session_state.front_matter.get("foreword")
                    if st.session_state.front_matter
                    else None,
                )
                st.session_state.epub_bytes = epub_bytes
                st.session_state.last_error = ""
            except Exception as exc:
                st.session_state.last_error = str(exc)
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
