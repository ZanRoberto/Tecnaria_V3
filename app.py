# ------------------- Fetch & Extract (codici prodotto) -------------------
_CODE_PATTERNS = {
    "ctf": re.compile(r"\bCTF\d{3}\b", re.IGNORECASE),
    "ctl": re.compile(r"\bCTL\d{3}\b", re.IGNORECASE),
    "ctcem": re.compile(r"\bCTCEM\d{3}\b", re.IGNORECASE),
    "vcem": re.compile(r"\bVCEM\d{3}\b", re.IGNORECASE),
    "diapason": re.compile(r"\bDIAPASON\d{2,3}\b", re.IGNORECASE),
}

GENERIC_CODE = re.compile(r"\b[A-Z]{2,8}\d{2,4}\b")

async def find_product_codes_from_web(query: str, family: str, max_links: int = 8) -> Tuple[List[str], List[str]]:
    q = f"site:tecnaria.com {query}"
    hits = await web_search(q, topk=max_links) or []
    pattern = _CODE_PATTERNS.get(family.lower()) or GENERIC_CODE
    codes = set()
    sources = []
    for h in hits[:max_links]:
        url = h.get("url") or ""
        if "tecnaria.com" not in url.lower():
            continue
        html = await _fetch_page_text(url)
        if not html:
            continue
        found = set(m.upper() for m in pattern.findall(html))
        if found:
            codes |= found
            sources.append(url)
    def _num(c):
        try:
            return int(re.findall(r"(\d{2,4})", c)[0])
        except Exception:
            return 0
    sorted_codes = sorted(codes, key=_num)
    return sorted_codes, sources[:5]

# ------------------- Dentro /api/ask -------------------
ql = user_q.lower()
if ("codici" in ql or "codice" in ql):
    fam = None
    if   "ctf"      in ql: fam = "ctf"
    elif "ctl"      in ql: fam = "ctl"
    elif "ctcem"    in ql: fam = "ctcem"
    elif "vcem"     in ql: fam = "vcem"
    elif "diapason" in ql: fam = "diapason"
    else: fam = "generic"
    codes, srcs = await find_product_codes_from_web(user_q, fam)
    if codes:
        lines = [f"- **{c}**" for c in codes]
        fontes = "\n".join(f"- {u}" for u in srcs) if srcs else "- tecnaria.com"
        answer = "OK\n" + "\n".join(lines) + f"\n\n**Fonti**\n{fontes}"
        return {"ok": True, "answer": answer}
