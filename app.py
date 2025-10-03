def web_lookup(q: str,
               min_score: float = MIN_WEB_SCORE,
               timeout: float = WEB_TIMEOUT,
               retries: int = WEB_RETRIES,
               domains: Optional[List[str]] = None) -> Tuple[str, List[str], float]:
    doms = domains or PREFERRED_DOMAINS
    sources: List[str] = []
    best_score = 0.0

    # se provider/chiavi mancano: esci subito
    if (SEARCH_PROVIDER == "brave" and not BRAVE_API_KEY) or (SEARCH_PROVIDER == "bing" and not BING_API_KEY):
        return "", [], 0.0

    for _ in range(retries + 1):
        results = web_search(q, topk=7) or []
        # preferisci domini Tecnaria/partner
        if doms:
            results = [r for r in results if any(d in domain_of(r.get("url","")) for d in doms)]
        ranked = rank_results(q, results)
        if not ranked:
            continue

        top = ranked[0]
        best_score = top.get("score", 0.0)
        if best_score < min_score:
            continue

        page_text = fetch_text(top.get("url",""), timeout=timeout)
        if not page_text:
            continue

        # SNIPPET VERO (non frase generica)
        snippet = short_text(page_text, 800)
        sources = [top.get("url","")]

        answer = (
            "OK\n"
            f"- **Riferimento**: {top.get('title') or 'pagina tecnica'}\n"
            f"- **Sintesi web**: {snippet}\n"
        )
        return answer, sources, best_score

    return "", sources, best_score
