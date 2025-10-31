# ------- OVERRIDE RULES (HIGH PRIORITY) -------
import re
from typing import Optional

# Regole: lista di dict. Puoi caricarle da static/data/overrides.json
OVERRIDE_RULES = [
    {
        "id": "OVR-CTF-CHIODATRICE-001",
        "family": "CTF",
        "patterns": [
            r"chiodatrice( a sparo)?( generica)?",
            r"normale chiodatrice",
            r"sparare ctf senza p560",
            r"posare .* ctf .* con chiodatrice"
        ],
        "answer": "⚠️ **NO.** I connettori **CTF Tecnaria** vanno posati **solo** con chiodatrice **SPIT P560** dotata di **kit/adattatore Tecnaria** e con **2 chiodi HSBR14 per connettore**.\n\n**Perché no altre chiodatrici?**\n- non garantiscono energia costante;\n- non assicurano perpendicolarità;\n- con lamiera non serrata causano rimbalzo.\n\n**Procedura (riassunto)**: serrare lamiera → appoggiare connettore → sparare con SPIT P560 + kit → verificare teste a filo piastra (±0,5 mm).",
        "mood": "alert",
        "intent": "errore",
        "priority": 100
    },
    {
        "id": "OVR-P560-TARATURA-001",
        "family": "P560",
        "patterns": [
            r"sbaglio se taro la p560",
            r"tarare la p560 con un solo tiro",
            r"taratura p560 (solo|1 tiro|un tiro)"
        ],
        "answer": "⚠️ **ATTENZIONE**\nSì, è un errore tarare la P560 con un solo tiro. La procedura Tecnaria prevede **2–3 tiri di prova** su superficie equivalente con le stesse cartucce. Controlla che le teste HSBR14 siano a filo piastra (±0,5 mm) e registra la taratura.",
        "mood": "alert",
        "intent": "errore",
        "priority": 100
    },
    {
        "id": "OVR-VCEM-NOP560-001",
        "family": "VCEM",
        "patterns": [
            r"posso usare la p560 per (i )?vcem",
            r"p560 vcem",
            r"usare p560 su vcem"
        ],
        "answer": "🔴 **NO.** I **VCEM** non si posano con P560. I VCEM sono fissaggi meccanici per laterocemento con foro piccolo (Ø8–9 mm) e si avvitano; la P560 è esclusa.",
        "mood": "alert",
        "intent": "errore",
        "priority": 100
    }
    # aggiungi altre regole qui...
]

def normalize_for_match(s: str) -> str:
    s = s or ""
    s = s.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s

def find_override(q: str) -> Optional[dict]:
    qn = normalize_for_match(q)
    # valutiamo tutte le regole, scegliamo la prima con maggior priority e match
    matches = []
    for rule in OVERRIDE_RULES:
        for pat in rule.get("patterns", []):
            try:
                if re.search(pat, qn, flags=re.IGNORECASE):
                    matches.append((rule.get("priority", 50), rule))
                    break
            except re.error:
                # pattern malformato, ignoralo
                continue
    if not matches:
        return None
    # prendi la regola con max priority (se tie, prima incontrata)
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]

# Nel tuo endpoint /qa/ask, prima di base_candidates, fai:
# override = find_override(q)
# if override:
#     return AskResponse(answer=override["answer"], score=round(override.get("priority", 100),3),
#                        family=override.get("family","COMM"),
#                        mood=override.get("mood","alert"),
#                        intent=override.get("intent","errore"),
#                        target=override.get("family"))
