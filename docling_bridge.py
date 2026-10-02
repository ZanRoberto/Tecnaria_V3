"""Docling extraction cache. No conversion during application startup or questions."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import tempfile


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
    payload = {'schema_version': 1, 'extractor': 'docling', 'pdf_sha256': source_hash,
               'pages': pages, 'document': document.export_to_dict()}
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
