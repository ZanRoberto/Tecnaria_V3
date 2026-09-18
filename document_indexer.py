"""Indicizzatore documentale universale per Narratore-Risponditore.

Il sistema analizza prima il documento, genera autonomamente un profilo di
indicizzazione e usa quel profilo per estrarre record indipendenti e verificabili.
Non contiene regole specifiche per LAGO o per un particolare settore.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pdfplumber
from pypdf import PdfReader


PROFILER_PROMPT = """
Sei il PROFILATORE DOCUMENTALE di un sistema universale di indicizzazione.
Devi esaminare un campione rappresentativo del documento e creare autonomamente
il miglior piano di estrazione possibile. Il piano sara' usato da un secondo
modello per trasformare ogni pagina in record autonomi e ricercabili.

Non presumere che il documento sia un listino. Potrebbe essere un catalogo,
manuale, normativa, contratto, procedura, ricerca, documento sanitario o altro.

Produci esclusivamente JSON valido con questa struttura:
{
  "document_type": "...",
  "document_title": "...",
  "languages": ["..."],
  "document_version": "... oppure null",
  "primary_entities": ["tipi di record da estrarre"],
  "required_fields": ["campi indispensabili per ogni record"],
  "optional_fields": ["campi utili quando presenti"],
  "identity_fields": ["campi che impediscono di confondere record diversi"],
  "relations_to_preserve": ["relazioni esplicite da conservare"],
  "page_reference_strategy": "come distinguere pagina PDF e pagina stampata",
  "table_strategy": "come ricostruire le righe senza mescolarle",
  "version_priority": "come gestire versioni, date e conflitti",
  "extraction_instructions": ["regole operative specifiche generate dal documento"],
  "risk_checks": ["controlli contro errori, unioni improprie e dati inventati"],
  "do_not_infer": ["informazioni che non devono essere dedotte"],
  "recommended_record_schema": {
    "campo_dinamico": "descrizione"
  }
}

Il piano deve obbligare l'estrattore a mantenere insieme codice, descrizione,
valori, unita', condizioni e pagina della stessa riga o entita'. Se un dato non
e' presente deve restare null: mai completarlo con conoscenze esterne.
"""


EXTRACTOR_PROMPT = """
Sei l'ESTRATTORE STRUTTURATO di un sistema documentale universale.
Ricevi un profilo creato automaticamente e alcune pagine delimitate.

REGOLE ASSOLUTE:
1. Usa esclusivamente il testo delle pagine ricevute.
2. Non unire mai dati di righe, prodotti, paragrafi o pagine differenti.
3. Ogni record deve essere autonomo: ripeti identita', contesto, unita', condizioni
   e riferimenti necessari anche quando nel documento sono impliciti nella tabella.
4. Conserva esattamente codici, numeri, prezzi, unita', date, versioni e negazioni.
5. Distingui sempre pdf_page da printed_page. Non inventare printed_page.
6. Se una tabella continua nella pagina seguente, crea record separati oppure una
   relazione esplicita; non fondere valori senza prova.
7. Non tradurre nomi propri, codici o valori. Conserva il testo sorgente rilevante.
8. Se una riga non e' ricostruibile con certezza, inseriscila in warnings e non
   trasformarla in un record certo.
9. Segui il profilo dinamico fornito sotto.

Produci esclusivamente JSON valido:
{
  "pages": [
    {
      "pdf_page": 1,
      "printed_page": "valore oppure null",
      "section": "... oppure null",
      "page_summary": "sintesi fedele, oppure null",
      "records": [
        {
          "record_type": "...",
          "record_id": "identificativo stabile ricavato dalla pagina",
          "title": "...",
          "fields": {"campo": "valore o null"},
          "relations": ["relazioni esplicite"],
          "source_excerpt": "breve estratto letterale che prova il record"
        }
      ],
      "warnings": ["incertezze reali"]
    }
  ]
}
"""


@dataclass
class PageText:
    pdf_page: int
    printed_page: Optional[str]
    text: str
    tables: List[List[List[Optional[str]]]]


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def detect_printed_page(text: str) -> Optional[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates = lines[:4] + lines[-4:]
    for line in candidates:
        if re.fullmatch(r"\d{1,4}", line):
            return line
    return None


def extract_pdf_pages(pdf_path: Path) -> List[PageText]:
    pages: List[PageText] = []
    # pdfplumber conserva meglio colonne e righe rispetto al solo testo lineare.
    # La lentezza aggiuntiva e' intenzionale: l'indicizzazione privilegia la qualita'.
    footer_reader = PdfReader(str(pdf_path), strict=False)
    with pdfplumber.open(str(pdf_path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = clean_text(page.extract_text(layout=True) or page.extract_text() or "")
            tables = page.extract_tables() or []
            footer_text = clean_text(footer_reader.pages[index - 1].extract_text() or "")
            printed_page = detect_printed_page(footer_text) or detect_printed_page(text)
            pages.append(PageText(index, printed_page, text, tables))
    return pages


def representative_sample(pages: List[PageText], limit: int = 24) -> List[PageText]:
    nonempty = [p for p in pages if len(p.text) >= 40]
    if len(nonempty) <= limit:
        return nonempty

    selected: Dict[int, PageText] = {}
    # Copertina/indice, distribuzione uniforme e pagine dense/tabellari.
    for page in nonempty[:3]:
        selected[page.pdf_page] = page
    step = max(1, len(nonempty) // 10)
    for page in nonempty[::step]:
        selected[page.pdf_page] = page
    dense = sorted(nonempty, key=lambda p: (len(re.findall(r"\d", p.text)), len(p.text)), reverse=True)
    for page in dense:
        selected[page.pdf_page] = page
        if len(selected) >= limit:
            break
    return [selected[n] for n in sorted(selected)][:limit]


def pages_payload(pages: Iterable[PageText], max_chars: int = 90000) -> str:
    chunks: List[str] = []
    used = 0
    for page in pages:
        block = (
            f"\n<<<PDF_PAGE {page.pdf_page}; PRINTED_PAGE "
            f"{page.printed_page or 'UNKNOWN'}>>>\n"
            f"TEXT_WITH_LAYOUT:\n{page.text}\n"
            f"TABLES_AS_ROWS:\n{json.dumps(page.tables, ensure_ascii=False)}\n"
            f"<<<END_PAGE>>>\n"
        )
        if used + len(block) > max_chars:
            break
        chunks.append(block)
        used += len(block)
    return "".join(chunks)


def parse_json_response(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def call_json(
    client: Any,
    model: str,
    instructions: str,
    input_text: str,
    max_output_tokens: int,
    retries: int = 3,
) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            response = client.responses.create(
                model=model,
                instructions=instructions,
                input=input_text,
                reasoning={"effort": "low"},
                max_output_tokens=max_output_tokens,
            )
            return parse_json_response(response.output_text or "")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Chiamata o JSON non valido dopo {retries} tentativi: {last_error}")


def profile_document(client: Any, model: str, pages: List[PageText]) -> Dict[str, Any]:
    sample = representative_sample(pages)
    return call_json(
        client,
        model,
        PROFILER_PROMPT,
        "CAMPIONE DEL DOCUMENTO:\n" + pages_payload(sample),
        max_output_tokens=3500,
    )


def batched(items: List[PageText], size: int) -> Iterable[List[PageText]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def safe_id(value: Any) -> str:
    text = str(value or "record").strip()
    text = re.sub(r"[^\w.-]+", "-", text, flags=re.UNICODE).strip("-")
    return text[:100] or "record"


def normalized_evidence(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def validate_extraction_result(
    result: Dict[str, Any],
    source_pages: List[PageText],
) -> Dict[str, Any]:
    """Rifiuta pagine inventate e segnala prove non rintracciabili."""
    expected = {page.pdf_page: page for page in source_pages}
    returned_pages = result.get("pages")
    if not isinstance(returned_pages, list):
        raise ValueError("Risposta priva dell'elenco pages")

    returned_numbers: List[int] = []
    for page_result in returned_pages:
        try:
            page_no = int(page_result.get("pdf_page"))
        except (TypeError, ValueError):
            raise ValueError("pdf_page non valido")
        if page_no not in expected:
            raise ValueError(f"Pagina non richiesta restituita dal modello: {page_no}")
        returned_numbers.append(page_no)
        source = expected[page_no]
        if source.printed_page is not None:
            page_result["printed_page"] = source.printed_page
        page_result.setdefault("warnings", [])
        source_blob = normalized_evidence(
            source.text + "\n" + json.dumps(source.tables, ensure_ascii=False)
        )
        records = page_result.get("records") or []
        if not isinstance(records, list):
            raise ValueError(f"records non valido a pagina {page_no}")
        for record in records:
            if not isinstance(record.get("fields") or {}, dict):
                raise ValueError(f"fields non valido a pagina {page_no}")
            excerpt = normalized_evidence(str(record.get("source_excerpt") or ""))
            if excerpt and excerpt not in source_blob:
                page_result["warnings"].append(
                    f"Estratto del record {record.get('record_id')} non ritrovato letteralmente nella pagina."
                )

    if sorted(returned_numbers) != sorted(expected):
        missing = sorted(set(expected) - set(returned_numbers))
        raise ValueError(f"Pagine mancanti nella risposta: {missing}")
    return result


def render_markdown(
    pdf_path: Path,
    profile: Dict[str, Any],
    page_results: List[Dict[str, Any]],
    failures: List[str],
) -> str:
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    out = [
        f"# INDICE STRUTTURATO: {profile.get('document_title') or pdf_path.name}",
        "",
        f"- SOURCE_FILE: {pdf_path.name}",
        f"- SOURCE_SHA256: {digest}",
        f"- DOCUMENT_TYPE: {profile.get('document_type')}",
        f"- DOCUMENT_VERSION: {profile.get('document_version')}",
        f"- LANGUAGES: {', '.join(profile.get('languages') or [])}",
        "",
        "## PROFILO DI INDICIZZAZIONE GENERATO AUTOMATICAMENTE",
        "",
        "```json",
        json.dumps(profile, ensure_ascii=False, indent=2),
        "```",
        "",
    ]

    for result in page_results:
        for page in result.get("pages", []):
            pdf_page = page.get("pdf_page")
            printed = page.get("printed_page")
            section = page.get("section")
            out.extend([
                "---",
                f"## PDF PAGE {pdf_page}" + (f" - PRINTED PAGE {printed}" if printed else ""),
                "",
                f"- PDF_PAGE: {pdf_page}",
                f"- PRINTED_PAGE: {printed if printed is not None else 'NOT_FOUND'}",
                f"- SECTION: {section if section is not None else 'NOT_FOUND'}",
                "",
            ])
            summary = page.get("page_summary")
            if summary:
                out.extend(["### PAGE SUMMARY", "", str(summary), ""])
            for ordinal, record in enumerate(page.get("records") or [], start=1):
                record_id = safe_id(record.get("record_id") or f"p{pdf_page}-{ordinal}")
                out.extend([
                    f"### RECORD {record_id}",
                    "",
                    f"- RECORD_TYPE: {record.get('record_type') or 'unspecified'}",
                    f"- TITLE: {record.get('title') or 'NOT_FOUND'}",
                    f"- SOURCE_PDF_PAGE: {pdf_page}",
                    f"- SOURCE_PRINTED_PAGE: {printed if printed is not None else 'NOT_FOUND'}",
                    f"- SOURCE_SECTION: {section if section is not None else 'NOT_FOUND'}",
                    "",
                    "#### FIELDS",
                    "",
                    "```json",
                    json.dumps(record.get("fields") or {}, ensure_ascii=False, indent=2),
                    "```",
                    "",
                ])
                relations = record.get("relations") or []
                if relations:
                    out.extend(["#### RELATIONS", ""] + [f"- {x}" for x in relations] + [""])
                excerpt = record.get("source_excerpt")
                if excerpt:
                    out.extend(["#### SOURCE EXCERPT", "", str(excerpt), ""])
            warnings = page.get("warnings") or []
            if warnings:
                out.extend(["### EXTRACTION WARNINGS", ""] + [f"- {x}" for x in warnings] + [""])

    if failures:
        out.extend(["---", "## PROCESSING FAILURES", ""] + [f"- {x}" for x in failures] + [""])
    return "\n".join(out).rstrip() + "\n"


def run_indexing(
    pdf_path: Path,
    output_path: Path,
    model: str,
    batch_size: int,
    checkpoint_path: Path,
    upload_vector_store: bool = False,
    printed_pages: Optional[set[str]] = None,
) -> None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY mancante")
    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    pages = extract_pdf_pages(pdf_path)
    usable = [p for p in pages if p.text]
    if printed_pages:
        usable = [p for p in usable if str(p.printed_page or "") in printed_pages]
        if not usable:
            raise RuntimeError(
                "Nessuna delle pagine stampate richieste e' stata riconosciuta nel PDF"
            )
    profile_path = output_path.with_suffix(".profile.json")
    if profile_path.exists():
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    else:
        profile = profile_document(client, model, pages)
        profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    completed: Dict[int, Dict[str, Any]] = {}
    failures: List[str] = []
    if checkpoint_path.exists():
        for line in checkpoint_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            for page in item.get("pages", []):
                completed[int(page["pdf_page"])] = item

    with checkpoint_path.open("a", encoding="utf-8") as checkpoint:
        for group in batched([p for p in usable if p.pdf_page not in completed], batch_size):
            dynamic_input = (
                "PROFILO DINAMICO GENERATO DAL DOCUMENTO:\n"
                + json.dumps(profile, ensure_ascii=False, indent=2)
                + "\n\nPAGINE DA ESTRARRE:\n"
                + pages_payload(group)
            )
            try:
                result = call_json(
                    client,
                    model,
                    EXTRACTOR_PROMPT,
                    dynamic_input,
                    max_output_tokens=7000,
                )
                result = validate_extraction_result(result, group)
                checkpoint.write(json.dumps(result, ensure_ascii=False) + "\n")
                checkpoint.flush()
                for page in result.get("pages", []):
                    completed[int(page["pdf_page"])] = result
            except Exception as exc:
                failures.append(f"PDF pages {[p.pdf_page for p in group]}: {exc}")

    unique_results: List[Dict[str, Any]] = []
    seen_objects: set[int] = set()
    for page_no in sorted(completed):
        obj = completed[page_no]
        marker = id(obj)
        if marker not in seen_objects:
            unique_results.append(obj)
            seen_objects.add(marker)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_markdown(pdf_path, profile, unique_results, failures),
        encoding="utf-8",
    )

    if upload_vector_store:
        vector_store_id = os.getenv("OPENAI_VECTOR_STORE_ID", "").strip()
        if not vector_store_id:
            raise RuntimeError("OPENAI_VECTOR_STORE_ID mancante per il caricamento finale")
        with output_path.open("rb") as stream:
            client.vector_stores.files.upload_and_poll(
                vector_store_id=vector_store_id,
                file=stream,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Indicizzatore universale auto-prompt")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=os.getenv("OPENAI_INDEX_MODEL", "gpt-5.6-sol"))
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument(
        "--printed-pages",
        help="Pagine stampate da indicizzare, separate da virgola (es. 31,36,43,51,52)",
    )
    parser.add_argument(
        "--upload-vector-store",
        action="store_true",
        help="Carica l'indice completato nel Vector Store configurato",
    )
    args = parser.parse_args()
    checkpoint = args.checkpoint or args.output.with_suffix(".checkpoint.jsonl")
    try:
        run_indexing(
            args.pdf,
            args.output,
            args.model,
            max(1, args.batch_size),
            checkpoint,
            upload_vector_store=args.upload_vector_store,
            printed_pages=(
                {x.strip() for x in args.printed_pages.split(",") if x.strip()}
                if args.printed_pages
                else None
            ),
        )
        print(args.output)
        return 0
    except Exception as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
