# -------------------------------------------------
# Intent router (robusto: compare prima di FAQ)
# -------------------------------------------------
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

    # --- regole "compare" robuste ---
    def _want_compare(x: str, y: str) -> bool:
        return (x in ql and y in ql) or bool(re.search(r"differenza\s+tra\s+.*\b" + re.escape(x) + r"\b.*\b" + re.escape(y) + r"\b", ql))

    # CTF vs CTL (case molto frequente)
    if _want_compare("ctf", "ctl"):
        # prova a trovare un compare precompilato
        found = None
        for it in CMP_ITEMS:
            fa = (it.get("famA") or "").upper()
            fb = (it.get("famB") or "").upper()
            if {fa, fb} == {"CTF", "CTL"}:
                found = it
                break
        if found:
            html = found.get("html") or ""
            text = found.get("answer") or ""
            src = "compare"
        else:
            ansA = _find_overview("CTF")
            ansB = _find_overview("CTL")
            html = _compare_html("CTF", "CTL", ansA, ansB)
            text = ""
            src = "synthetic"
        return {
            "ok": True, "match_id": "COMPARE::CTF_VS_CTL", "lang": lang,
            "family": "CTF+CTL", "intent": "compare", "source": src, "score": 95.0,
            "text": text, "html": html
        }

    # 1) Confronti A vs B per qualunque famiglia (generico)
    fams = list(FAM_TOKENS.keys())
    for i, a in enumerate(fams):
        for b in fams[i + 1:]:
            if a.lower() in ql and b.lower() in ql:
                found = None
                for it in CMP_ITEMS:
                    fa = (it.get("famA") or "").upper()
                    fb = (it.get("famB") or "").upper()
                    if {fa, fb} == {a, b}:
                        found = it
                        break
                if found:
                    html = found.get("html") or ""
                    text = found.get("answer") or ""
                    src = "compare"
                else:
                    ansA = _find_overview(a)
                    ansB = _find_overview(b)
                    html = _compare_html(a, b, ansA, ansB)
                    text = ""
                    src = "synthetic"
                return {
                    "ok": True, "match_id": f"COMPARE::{a}_VS_{b}", "lang": lang,
                    "family": f"{a}+{b}", "intent": "compare", "source": src, "score": 92.0,
                    "text": text, "html": html,
                }

    # 2) Famiglia singola → prova FAQ poi overview
    scored = [(fam, _score_tokens(ql, toks)) for fam, toks in FAM_TOKENS.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    fam, s = scored[0]
    if s >= 0.2:
        for r in FAQ_BY_LANG.get(lang, []):
            keys = ((r.get("tags") or "") + " " + (r.get("question") or "")).lower()
            toks = re.split(r"[,\s;/\-]+", keys)
            if _score_tokens(ql, toks) >= 0.25:
                return {
                    "ok": True,
                    "match_id": r.get("id") or f"FAQ::{fam}",
                    "lang": lang, "family": fam, "intent": "faq",
                    "source": "faq", "score": 88.0,
                    "text": r.get("answer") or "", "html": ""
                }
        ov = _find_overview(fam)
        return {
            "ok": True, "match_id": f"OVERVIEW::{fam}", "lang": lang,
            "family": fam, "intent": "overview", "source": "overview",
            "score": 75.0, "text": ov, "html": ""
        }

    # 3) Fallback
    return {
        "ok": True, "match_id": "<NULL>", "lang": lang,
        "family": "", "intent": "fallback", "source": "fallback", "score": 0,
        "text": "Non ho trovato una risposta diretta nei metadati locali. Specifica meglio la famiglia/prodotto.",
        "html": ""
    }
