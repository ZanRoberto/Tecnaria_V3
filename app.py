# -----------------------------------------------------------------------------
# WEB SEARCH / FETCH (rigorosa su domini ufficiali + fallback Sinapsi)
# -----------------------------------------------------------------------------

BAD_DOMAINS = (
    "arredamento.it", "reddit.com", "facebook.com", "pinterest.", "quora.com",
    "amazon.", "ebay.", "alibaba.", "tiktok.", "instagram.", "twitter.", "x.com",
    "issuu.com", "scribd.com", "slideshare.net", "yumpu.com"
)

def is_bad_domain(url: str) -> bool:
    d = domain_of(url)
    return any(bad in d for bad in BAD_DOMAINS)

def allowed_domain(url: str, prefer: List[str]) -> bool:
    d = domain_of(url)
    if not d or is_bad_domain(url):
        return False
    # preferiti forti: tecnaria/spit/spitpaslode
    if any(p in d for p in prefer):
        return True
    return False  # blocchiamo TUTTO il resto

def brave_search(q: str, topk: int = 5, timeout: float = WEB_TIMEOUT) -> List[Dict]:
    if not BRAVE_API_KEY:
        return []
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": q, "count": topk}
    url = "https://api.search.brave.com/res/v1/web/search"
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        items = []
        for it in data.get("web", {}).get("results", []):
            items.append({
                "title": it.get("title") or "",
                "url": it.get("url") or "",
                "snippet": it.get("description") or ""
            })
        return items
    except Exception as e:
        if DEBUG:
            print("[BRAVE][ERR]", e)
        return []

def bing_search(q: str, topk: int = 5, timeout: float = WEB_TIMEOUT) -> List[Dict]:
    key = BING_API_KEY
    endpoint = SEARCH_API_ENDPOINT or "https://api.bing.microsoft.com/v7.0/search"
    if not key:
        return []
    headers = {"Ocp-Apim-Subscription-Key": key}
    params = {"q": q, "count": topk, "responseFilter": "Webpages"}
    try:
        r = requests.get(endpoint, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        items = []
        for it in data.get("webPages", {}).get("value", []):
            items.append({
                "title": it.get("name") or "",
                "url": it.get("url") or "",
                "snippet": it.get("snippet") or ""
            })
        return items
    except Exception as e:
        if DEBUG:
            print("[BING][ERR]", e)
        return []

def web_search(q: str, topk: int = 7) -> List[Dict]:
    return bing_search(q, topk=topk) if SEARCH_PROVIDER == "bing" else brave_search(q, topk=topk)

def fetch_text(url: str, timeout: float = WEB_TIMEOUT) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script","style","noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()
    except Exception as e:
        if DEBUG:
            print("[FETCH][ERR]", url, e)
        return ""

def rank_results(q: str, results: List[Dict], prefer_domains: List[str]) -> List[Dict]:
    nq = normalize(q)
    ranked = []
    for it in results:
        url = it.get("url","")
        if not allowed_domain(url, prefer_domains):
            continue
        score = 0.0
        score += prefer_score_for_domain(url)
        sn = normalize((it.get("title") or "") + " " + (it.get("snippet") or ""))
        for w in set(nq.split()):
            if w and w in sn:
                score += 0.35
        if P560_PAT.search(sn):
            score += 0.35
        it["score"] = score
        ranked.append(it)
    ranked.sort(key=lambda x: x.get("score",0.0), reverse=True)
    return ranked

def web_lookup_strict(q: str,
                      prefer_domains: Optional[List[str]] = None,
                      min_score: float = None,
                      retries: int = None) -> Tuple[str, List[str], float]:
    """
    Cerca SOLO su domini preferiti (Tecnaria/Spit). NIENTE forum/spazzatura.
    Se non trova nulla sopra soglia -> ritorna stringa vuota e fonti vuote.
    """
    doms = prefer_domains or PREFERRED_DOMAINS
    min_s = min_score if min_score is not None else max(MIN_WEB_SCORE, 0.55)
    tries = retries if retries is not None else max(WEB_RETRIES, 1)

    best_score = 0.0
    for _ in range(tries + 1):
        raw = web_search(q, topk=8)
        ranked = rank_results(q, raw, doms)
        if DEBUG:
            print(f"[WEB][STRICT] ranked={len(ranked)} min_s={min_s}")
        if not ranked:
            continue
        top = ranked[0]
        best_score = float(top.get("score",0.0))
        if best_score < min_s:
            if DEBUG:
                print(f"[WEB][STRICT] best below min_s: {best_score:.2f} < {min_s:.2f}")
            continue
        txt = fetch_text(top["url"], timeout=WEB_TIMEOUT)
        if not txt:
            continue
        # sintetizza in stile “bot”
        ans = (
            "OK\n"
            f"- **Riferimento**: {top.get('title') or 'documentazione ufficiale'}\n"
            "- **Sintesi**: contenuti tecnici pertinenti trovati su fonte ufficiale.\n"
        )
        return ans, [top["url"]], best_score

    return "", [], best_score

# -----------------------------------------------------------------------------
# SINAPSI – fallback helper super-semplice (usa il tuo brain già caricato)
# -----------------------------------------------------------------------------
_sinapsi_cache = None

def _load_sinapsi() -> List[Dict]:
    global _sinapsi_cache
    if _sinapsi_cache is not None:
        return _sinapsi_cache
    try:
        base = os.getenv("CRITICI_DIR","").strip()
        if not base:
            _sinapsi_cache = []
            return _sinapsi_cache
        # cerca tutti i json sinapsi_*.json
        paths = sorted(glob.glob(os.path.join(base, "sinapsi*.json")))
        brain: List[Dict] = []
        for p in paths:
            with open(p,"r",encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                brain.append(data)
            elif isinstance(data, list):
                brain.extend(data)
        # precompila regex
        for item in brain:
            pat = item.get("pattern","")
            try:
                item["_rx"] = re.compile(pat, re.I)
            except re.error:
                item["_rx"] = None
        _sinapsi_cache = brain
        if DEBUG:
            print(f"[SINAPSI] loaded {len(brain)} entries from {base}")
    except Exception as e:
        if DEBUG:
            print("[SINAPSI][ERR]", e)
        _sinapsi_cache = []
    return _sinapsi_cache

def try_sinapsi(q: str) -> Optional[str]:
    brain = _load_sinapsi()
    nq = q.strip()
    for item in brain:
        rx = item.get("_rx")
        if not rx:
            continue
        if rx.search(nq):
            mode = (item.get("mode") or "override").lower()
            ans = item.get("answer","").rstrip()
            # rispetta lo stile “OK\n- ...” già pronto nel brain
            return ans if ans else None
    return None

# -----------------------------------------------------------------------------
# ROUTING – web ufficiale -> Sinapsi -> KB -> contatti
# -----------------------------------------------------------------------------
def route_question_to_answer(raw_q: str) -> str:
    if not raw_q or not raw_q.strip():
        return "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"

    cleaned = clean_ui_noise(raw_q)
    nq = normalize(cleaned)

    # 1) Se l’utente chiede contatti esplicitamente e non demotizziamo
    if CONT_PAT.search(nq) and not DEMOTE_CONTACTS:
        return answer_contacts()

    # 2) Caso speciale P560 + (patentino|formazione) -> tentiamo web ufficiale, poi Sinapsi
    if FORCE_P560_WEB and P560_PAT.search(nq) and LIC_PAT.search(nq):
        web_ans, srcs, sc = web_lookup_strict(cleaned, prefer_domains=PREFERRED_DOMAINS)
        if web_ans:
            return build_p560_from_web(srcs)
        sin = try_sinapsi(cleaned)
        if sin:
            return sin
        return build_p560_from_web(srcs)  # template sicuro

    # 3) WEB STRICT (solo domini ufficiali)
    web_ans, srcs, sc = web_lookup_strict(cleaned, prefer_domains=PREFERRED_DOMAINS)
    if web_ans:
        return format_as_bot(web_ans, srcs)

    # 4) FALLBACK SINAPSI (se c’è una regola pertinente)
    sin = try_sinapsi(cleaned)
    if sin:
        return sin

    # 5) KB locale (tenendo giù i contatti se richiesto)
    local = kb_lookup(cleaned, exclude_contacts=DEMOTE_CONTACTS)
    if local:
        return format_as_bot("OK\n- **Riferimento locale** trovato.\n- **Sintesi**: " + short_text(local, 800))

    # 6) Contatti solo se richiesti e demotizzati
    if CONT_PAT.search(nq) and DEMOTE_CONTACTS:
        return answer_contacts()

    # 7) Fallback elegante
    return (
        "OK\n"
        "- **Non ho trovato una risposta affidabile su fonte ufficiale**. "
        "Posso cercare meglio, oppure metterti in contatto con un tecnico.\n"
    )
