"""Docling extraction cache. No conversion during application startup or questions."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import tempfile
import re


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_cache(cache_path, pdf_path):
    if not Path(cache_path).is_file():
        return {}, 'not_generated'
    if not Path(pdf_path).is_file():
        return {}, 'source_pdf_missing'
    try:
        data = json.loads(Path(cache_path).read_text(encoding='utf-8'))
        if data.get('pdf_sha256') != sha256_file(pdf_path):
            return {}, 'source_mismatch'
        pages = {int(k): v for k, v in data['pages'].items() if isinstance(v, str) and v.strip()}
        return pages, 'ready' if pages else 'empty'
    except (ValueError, KeyError, OSError):
        return {}, 'invalid_cache'


def extract_cards(document, source_hash):
    """Bind values only through explicit table coordinates; reject ambiguous tables."""
    cards, rejected = [], []
    texts = document.get('texts', [])
    tables = document.get('tables', [])
    titles = {}
    heading = ''
    for child in document.get('body', {}).get('children', []):
        ref = child.get('$ref', '')
        if ref.startswith('#/texts/'):
            item = texts[int(ref.rsplit('/', 1)[1])]
            if item.get('label') in ('section_header', 'title'):
                heading = item.get('text', '')
        elif ref.startswith('#/tables/'):
            titles[int(ref.rsplit('/', 1)[1])] = heading
    for index, table in enumerate(tables):
        try:
            pages = {p['page_no'] for p in table.get('prov', [])}
            if len(pages) != 1:
                raise ValueError('pagina assente o tabella su più pagine')
            page = next(iter(pages))
            data = table.get('data', {})
            cells = data.get('table_cells', [])
            grid = {}
            for cell in cells:
                r0, r1 = cell['start_row_offset'], cell['end_row_offset']
                c0, c1 = cell['start_col_offset'], cell['end_col_offset']
                if r1 <= r0 or c1 <= c0:
                    raise ValueError('coordinate invalide')
                for row in range(r0, r1):
                    for col in range(c0, c1):
                        if (row, col) in grid:
                            raise ValueError('celle sovrapposte')
                        grid[row, col] = cell
            header_rows = {r for (r, c), cell in grid.items() if cell.get('column_header')}
            if not header_rows:
                raise ValueError('intestazioni non identificate')
            last_header = max(header_rows)
            headers = {}
            for col in range(data.get('num_cols', max((c for r,c in grid), default=-1)+1)):
                parts = list(dict.fromkeys(grid[r,col]['text'].strip() for r in sorted(header_rows)
                             if (r,col) in grid and grid[r,col]['text'].strip()))
                headers[col] = ' / '.join(parts)
            code_cols = [c for c,h in headers.items() if re.search(r'\b(code|codice|sku|articolo|item code)\b', h, re.I)]
            if len(code_cols) != 1:
                raise ValueError('colonna codice assente o ambigua')
            noncode = [h for c,h in headers.items() if c != code_cols[0]]
            if any(not h for h in noncode) or len(set(noncode)) != len(noncode):
                raise ValueError('intestazioni vuote o duplicate')
            title = titles.get(index, '')
            if not title:
                raise ValueError('titolo prodotto non documentato')
            pending = []
            for row in sorted({r for r,c in grid if r > last_header}):
                code_cell = grid.get((row, code_cols[0]))
                if not code_cell:
                    raise ValueError('riga senza codice')
                code = code_cell['text'].strip()
                if not re.fullmatch(r'(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{4,}', code):
                    raise ValueError('codice non univoco')
                attributes, prices, evidence = {}, {}, {}
                for col, header in headers.items():
                    if col == code_cols[0]:
                        continue
                    cell = grid.get((row,col))
                    if cell is None or not cell['text'].strip():
                        continue
                    value = cell['text'].strip()
                    is_price = bool(re.search(r'prezzo|price|premio|premium|euro|eur|€|laccato|lacquer|glass|vetro|wildwood',header,re.I))
                    if is_price and not re.fullmatch(r'(?:€\s*)?\d+(?:\.\d{3})*(?:,\d{1,2})?(?:\s*(?:€|EUR|euro))?',value,re.I):
                        raise ValueError('prezzo non numerico o ambiguo')
                    (prices if is_price else attributes)[header] = value
                    evidence[header] = {k: cell.get(k) for k in ('start_row_offset','end_row_offset','start_col_offset','end_col_offset','bbox')}
                pending.append({'codice': code, 'pagina': page, 'prodotto': title,
                    'attributi': attributes, 'prezzi': prices,
                    'provenienza': {'extractor': 'docling', 'pdf_sha256': source_hash,
                                   'table_index': index, 'row': row, 'cells': evidence}})
            cards.extend(pending)
        except (KeyError, ValueError, TypeError, IndexError) as error:
            rejected.append({'table_index': index, 'reason': str(error)})
    return cards, {'tables_total': len(tables), 'cards_accepted': len(cards), 'tables_rejected': rejected}


def read_cards(cache_path):
    """Caller must first validate the cache hash with read_cache."""
    try:
        data = json.loads(Path(cache_path).read_text(encoding='utf-8'))
        return extract_cards(data.get('document', {}), data['pdf_sha256'])
    except (OSError, ValueError, KeyError) as error:
        return [], {'error': str(error)}


def generate(pdf_path, output_path):
    from docling.document_converter import DocumentConverter
    pdf_path, output_path = Path(pdf_path), Path(output_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    source_hash = sha256_file(pdf_path)
    result = DocumentConverter().convert(pdf_path)
    status = getattr(result.status, 'value', str(result.status))
    if str(status).lower() != 'success':
        raise RuntimeError(f'Docling conversion incomplete: {status}; cache not replaced')
    document = result.document
    pages = {str(p): document.export_to_markdown(page_no=int(p)) for p in sorted(document.pages)}
    if not pages or any(not text.strip() for text in pages.values()):
        raise RuntimeError('Empty pages detected; inspect document before activating cache')
    if sha256_file(pdf_path) != source_hash:
        raise RuntimeError('PDF changed during conversion')
    cards, report = extract_cards(document.export_to_dict(), source_hash)
    payload = {'schema_version': 2, 'extractor': 'docling', 'pdf_sha256': source_hash,
               'pages': pages, 'document': document.export_to_dict(),
               'cards': cards, 'card_report': report}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=output_path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False)
        os.replace(temporary, output_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f'Docling: {len(pages)} pages saved to {output_path}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--output', default='static/data/docling_document.json')
    args = parser.parse_args()
    generate(args.pdf, args.output)
