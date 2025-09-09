# knowledge_loader.py
import os, re, time, json
from pathlib import Path
from bs4 import BeautifulSoup

try:
    import pypdf
    HAS_PDF = True
except Exception:
    HAS_PDF = False

TEXT_EXT = {".txt", ".md", ".csv"}
HTML_EXT = {".html", ".htm"}
PDF_EXT  = {".pdf"}

def _read_text(fp: Path) -> str:
    try:
        return fp.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return fp.read_text(errors="ignore")

def _read_html(fp: Path) -> str:
    raw = _read_text(fp)
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script","style","iframe","noscript"]):
        tag.decompose()
    text = soup.get_text(" ")
    return re.sub(r"\s+", " ", text).strip()

def _read_pdf(fp: Path) -> str:
    if not HAS_PDF:
        return ""
    try:
        reader = pypdf.PdfReader(str(fp))
        pages = [p.extract_text() or "" for p in reader.pages]
        text = "\n".join(pages)
        return re.sub(r"\s+", " ", text).strip()
    except Exception:
        return ""

def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def walk_knowledge(base_dir: str) -> list[dict]:
    """
    Ritorna una lista di record: [{path, relpath, mtime, text}, ...]
    Scende nelle sottocartelle e legge i formati supportati.
    """
    base = Path(base_dir).resolve()
    out = []
    for fp in base.rglob("*"):
        if not fp.is_file(): 
            continue
        ext = fp.suffix.lower()
        text = ""
        if ext in TEXT_EXT:
            text = _read_text(fp)
        elif ext in HTML_EXT:
            text = _read_html(fp)
        elif ext in PDF_EXT:
            text = _read_pdf(fp)
        if not text:
            continue
        out.append({
            "path": str(fp),
            "relpath": str(fp.relative_to(base)),
            "mtime": fp.stat().st_mtime,
            "text": _normalize(text)
        })
    return out

# cache leggera su disco (rigenera solo se cambiano i file)
def load_with_cache(base_dir: str, cache_path: str = ".knowledge_cache.json") -> list[dict]:
    try:
        cache = json.loads(Path(cache_path).read_text())
    except Exception:
        cache = {}
    recs = walk_knowledge(base_dir)
    # se mtime+dimensione non cambiano, riusa testo (utile per pdf/html pesanti)
    changed = False
    for r in recs:
        key = r["path"]
        meta = {"mtime": r["mtime"], "size": Path(r["path"]).stat().st_size}
        if key in cache and cache[key].get("meta")==meta and cache[key].get("text"):
            r["text"] = cache[key]["text"]
        else:
            changed = True
            cache[key] = {"meta": meta, "text": r["text"], "relpath": r["relpath"]}
    if changed:
        try:
            Path(cache_path).write_text(json.dumps(cache, ensure_ascii=False))
        except Exception:
            pass
    return recs
