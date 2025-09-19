def p560_answer(mode: str) -> str:
    if mode == "breve":
        return (
            "<p><strong>SPIT P560</strong> è la chiodatrice a polvere per la posa rapida e affidabile dei connettori Tecnaria su acciaio e calcestruzzo. "
            "Riduce i tempi in cantiere e standardizza il fissaggio attraverso la lamiera grecata. "
            "Usare sempre i DPI e seguire il manuale operativo.</p>"
        )
    if mode == "standard":
        return (
            "<h3>P560 — cosa fa e quando usarla</h3>"
            "<p>Chiodatrice a polvere per fissaggi strutturali: tipicamente per <em>posa dei connettori CTF</em> su travi in acciaio (attraverso lamiera) "
            "e per fissaggi su calcestruzzo non fessurato. La scelta di <strong>chiodi</strong> e <strong>cariche</strong> dipende dal supporto; "
            "eseguire sempre prove sul materiale reale. Mantenere appoggio perpendicolare, pressione piena, tiro controllato. "
            "DPI obbligatori, manutenzione regolare (pulizia camera di scoppio).</p>"
        )
    # C dettagliata (scheda “narrata”, ricca)
    return (
        "<h3>P560 — guida d’uso tecnica</h3>"
        "<p><strong>Scopo:</strong> fissaggio dei connettori Tecnaria su acciaio/lamiera grecata e su calcestruzzo, con qualità e ripetibilità del tiro.</p>"
        "<h4>Selezione consumabili</h4>"
        "<ul>"
        "<li><strong>Chiodi:</strong> lunghezza/diametro compatibili con spessori e supporto; verifica della penetrazione effettiva su provino.</li>"
        "<li><strong>Cariche:</strong> graduazione in funzione della durezza del supporto; salire di passo <em>solo</em> se la prova risulta sotto-tiro.</li>"
        "</ul>"
        "<h4>Procedura operativa</h4>"
        "<ul>"
        "<li>Appoggio perpendicolare sulla superficie; accertare l’assenza di luce tra lamiera e trave.</li>"
        "<li>Pressione piena, attivazione controllata; controllo immediato del tiro e della stabilità.</li>"
        "<li>Ripetere la prova quando si cambia carica, fornitura di chiodi o tipo di supporto.</li>"
        "</ul>"
        "<h4>Controlli di cantiere</h4>"
        "<ul>"
        "<li>Verifica visiva del gambo, rosetta e centratura in gola; campionamento di fissaggi ogni tratto omogeneo.</li>"
        "<li>Tracciamento passi e rispetto interassi; registrazione di eventuali ritiri o colpi nulli.</li>"
        "</ul>"
        "<h4>Sicurezza e manutenzione</h4>"
        "<ul>"
        "<li>DPI (occhi/udito/mani), delimitazione dell’area di tiro, attrezzo in efficienza.</li>"
        "<li>Pulizia periodica della camera di scoppio, cursori e otturatori; sostituzione parti usurate secondo manuale.</li>"
        "</ul>"
        "<h4>Integrazione col sistema</h4>"
        "<p>Per i <strong>CTF su lamiera</strong>: garantire corrugamento corretto, centratura in gola, coerenza con i passi di progetto. "
        "Riferimenti: manuale Tecnaria, EC4, istruzioni del produttore dell’attrezzo.</p>"
    )

def ctf_answer_info(mode: str) -> str:
    if mode == "breve":
        return (
            "<p>I connettori <strong>CTF</strong> rendono collaborante il solaio acciaio-calcestruzzo, aumentando rigidezza e capacità. "
            "Sono certificati ETA e si posano rapidamente, anche attraverso lamiera grecata.</p>"
        )
    if mode == "standard":
        return (
            "<h3>CTF — cosa sono e come si usano</h3>"
            "<p>Pioli/fissaggi per solai collaboranti acciaio-cls: trasferiscono il taglio tra trave e soletta. "
            "La verifica di progetto si fa con <strong>PRd</strong> da tabelle/ETA o con la regola <strong>P₀×k<sub>t</sub></strong> su lamiera; "
            "la posa avviene con chiodatrice a polvere (es. P560), rispettando passi e interassi previsti.</p>"
        )
    return (
        "<h3>CTF — guida tecnica sintetica</h3>"
        "<h4>Impiego</h4>"
        "<p>Travi in acciaio con lamiera grecata o soletta piena; trasferimento del taglio per comportamento collaborante.</p>"
        "<h4>Progetto e verifiche</h4>"
        "<ul>"
        "<li><strong>PRd per connettore</strong>: da tabelle (soletta piena) o <em>P₀×k<sub>t</sub></em> su lamiera, con dipendenza da cls, profilo, spessore <em>t</em>, nr/gola.</li>"
        "<li><strong>Capacità per metro</strong> = PRd × n/m (n/m = 1000 / passo lungo trave).</li>"
        "<li><strong>Criterio EC4</strong>: capacità per metro ≥ V<sub>L,Ed</sub> × margine.</li>"
        "</ul>"
        "<h4>Posa</h4>"
        "<p>P560, centratura in gola, rispetto dei passi e delle distanze da estremità; DPI e controlli di cantiere.</p>"
        "<h4>Riferimenti</h4>"
        "<p>ETA-18/0447, EC4, manuale Tecnaria.</p>"
    )

def ctl_answer_info(mode: str) -> str:
    if mode == "breve":
        return (
            "<p>I connettori <strong>CTL</strong> collegano legno e calcestruzzo, migliorando rigidezza e comfort del solaio. "
            "Soluzione certificata e collaudata per sistemi collaboranti.</p>"
        )
    if mode == "standard":
        return (
            "<h3>CTL — quando e perché</h3>"
            "<p>Per sistemi legno-cls (o acciaio-legno), con dimensionamento tramite tabelle Tecnaria e verifiche EC5/EC4. "
            "Posa con viti dedicate e controlli di scorrimento/ancoraggio; attenzione alle deformazioni a lungo termine.</p>"
        )
    return (
        "<h3>CTL — guida tecnica</h3>"
        "<h4>Parametri di progetto</h4>"
        "<ul>"
        "<li>Specie e classe del legno; spessore della soletta; interassi e schema di posa.</li>"
        "<li>Verifiche EC5/EC4, stato limite di esercizio (fessurazioni, scorrimenti) e deformabilità.</li>"
        "</ul>"
        "<h4>Posa e controlli</h4>"
        "<p>Viti/staffe dedicate, tracciamento degli interassi, DPI, registrazione dei controlli di tiro/estrazione dove previsti.</p>"
    )

def ceme_answer_info(mode: str) -> str:
    if mode == "breve":
        return (
            "<p><strong>CEM-E</strong> collega un getto di calcestruzzo nuovo a uno esistente, assicurando continuità strutturale. "
            "È la scelta tipica per ampliamenti e risanamenti.</p>"
        )
    if mode == "standard":
        return (
            "<h3>CEM-E — uso tipico</h3>"
            "<p>Connettori cls/cls posati a foro con resina o ancorante certificato. "
            "Verifiche su resistenze del supporto e ancoraggio, secondo ETA e norme locali. Posa con foratura, pulizia foro e iniezione controllata.</p>"
        )
    return (
        "<h3>CEM-E — guida tecnica</h3>"
        "<h4>Parametri chiave</h4>"
        "<ul>"
        "<li>Resistenza del cls esistente e del nuovo; profondità di ancoraggio; diametro foro; tipo di resina.</li>"
        "<li>Controlli pull-out a campione in cantiere, laddove richiesti.</li>"
        "</ul>"
        "<h4>Procedura di posa</h4>"
        "<p>Foratura Ø definito, pulizia con aria/spazzola, iniezione resina, inserimento connettore, tempi di attesa, collaudo.</p>"
    )

def diapason_answer_info(mode: str) -> str:
    if mode == "breve":
        return (
            "<p><strong>Diapason</strong> consente il rinforzo di solai esistenti con interventi poco invasivi, "
            "migliorando rigidezza e capacità senza demolizioni estese.</p>"
        )
    if mode == "standard":
        return (
            "<h3>Diapason — campo di applicazione</h3>"
            "<p>Connettore a lamiera sagomata per riqualifica/adeguamento; distribuzione diffusa dei carichi; "
            "posa con chiodi/ancoranti e integrazione nel getto. Ideale in ristrutturazioni con spessori limitati.</p>"
        )
    return (
        "<h3>Diapason — guida tecnica</h3>"
        "<h4>Progetto</h4>"
        "<ul>"
        "<li>Geometria del connettore, spessori lamiera, barre di ripartizione (Ø8–Ø10) e passo.</li>"
        "<li>Verifiche: trasferimento taglio, compatibilità col cls esistente, dettagli di ancoraggio.</li>"
        "</ul>"
        "<h4>Posa</h4>"
        "<p>Tracciatura, fissaggio meccanico o con chiodatrice, controllo interassi; DPI e collaudo visivo/funzionale.</p>"
    )

def tpl_ctf_calc(mode: str, p: dict, h_cap: str, note: str | None=None) -> str:
    # tutti HTML “narrativi”
    if mode == "breve":
        return (
            f"<p><strong>Consiglio:</strong> CTF <strong>{h_cap}</strong>, dimensionato su domanda e combinazione indicate. "
            f"{(' '+note) if note else ''}</p>"
        )
    if mode == "standard":
        return (
            "<h3>Scelta altezza CTF</h3>"
            f"<p><strong>Dati:</strong> lamiera H{p.get('h_lamiera','—')} ({p.get('dir','—')}), passo gola {p.get('passo','—')} mm; "
            f"soletta {p.get('s_soletta','—')} mm; passo lungo trave {p.get('s_long','—')} mm; "
            f"V<sub>L,Ed</sub>={p.get('vled','—')} kN/m; cls {p.get('cls','—')}.</p>"
            f"<p><strong>Esito:</strong> CTF <strong>{h_cap}</strong>.</p>"
            f"{('<p><em>Nota:</em> '+note+'</p>') if note else ''}"
        )
    # dettagliata (C) — scheda corposa
    return (
        "<h3>Verifica connettori CTF — esito e motivazione</h3>"
        "<h4>Input di progetto</h4>"
        f"<ul>"
        f"<li>Lamiera H{p.get('h_lamiera','—')} ({p.get('dir','—')}), passo gola {p.get('passo','—')} mm; "
        f"t={p.get('t_lamiera','—')} mm; nr={p.get('nr_gola','—')}/gola</li>"
        f"<li>Soletta {p.get('s_soletta','—')} mm; passo lungo trave {p.get('s_long','—')} mm</li>"
        f"<li>V<sub>L,Ed</sub>={p.get('vled','—')} kN/m; cls {p.get('cls','—')}</li>"
        f"</ul>"
        "<h4>Metodo di verifica</h4>"
        "<ul>"
        "<li>PRd da tabelle/ETA (soletta piena) oppure P₀×k<sub>t</sub> su lamiera, con dipendenza da t e nr/gola.</li>"
        "<li>Capacità per metro = PRd × (1000 / passo lungo trave).</li>"
        "<li>Criterio: capacità ≥ domanda × margine.</li>"
        "</ul>"
        f"<h4>Esito tecnico</h4><p>Altezza consigliata: <strong>{h_cap}</strong>.</p>"
        f"{('<p><em>Nota:</em> '+note+'</p>') if note else ''}"
        "<h4>Raccomandazioni</h4>"
        "<ul>"
        "<li>Controllare interassi, distanze da estremità e staffe secondo manuale Tecnaria.</li>"
        "<li>Verificare coerenza passo in gola ↔ direzione lamiera e centratura in gola.</li>"
        "</ul>"
        "<h4>Riferimenti</h4>"
        "<p>ETA-18/0447, EC4; posa tramite P560.</p>"
    )
