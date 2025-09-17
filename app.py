import os, re, glob, logging
from flask import Flask, request, jsonify, Response, redirect
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from rapidfuzz import fuzz  # fuzzy match note locali

# =============== Logging & Flask ===============
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
app = Flask(__name__)
CORS(app, resources={r"/ask": {"origins": "*"}})

# =============== ENV ===============
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL   = os.environ.get("OPENAI_MODEL", "gpt-4o")
NOTE_DIR       = os.environ.get("NOTE_DIR", "documenti_gTab")

def _parse_float(val, default=0.0):
    try:
        if val is None: return default
        v = str(val).strip().lower()
        if v in ("", "none", "null", "nil"): return default
        return float(v)
    except Exception:
        return default

# 0 => non passare temperature (molti modelli vogliono default=1)
TEMPERATURE = _parse_float(os.environ.get("OPENAI_TEMPERATURE"), 0.0)

# =============== OpenAI client (nuovo/legacy) ===============
NEW_SDK = True
openai = None
client = None
try:
    from openai import OpenAI  # >=1.x (Responses API)
    client = OpenAI(api_key=OPENAI_API_KEY)
    logging.info("OpenAI SDK: NEW (>=1.x) — Responses API")
except Exception:
    import openai as _openai  # <=0.28.x (Chat Completions)
    openai = _openai
    NEW_SDK = False
    if OPENAI_API_KEY:
        openai.api_key = OPENAI_API_KEY
    logging.info("OpenAI SDK: LEGACY (<=0.28.x) — Chat Completions")

# =============== Dati aziendali CERTI (no web) ===============
TECNARIA_CONTACT = {
    "ragione_sociale": "TECNARIA S.p.A.",
    "indirizzo": "Viale Pecori Giraldi, 55 – 36061 Bassano del Grappa (VI)",
    "piva_cf": "01277680243",
    "telefono": "+39 0424 502029",
    "fax": "+39 0424 502386",
    "email": "info@tecnaria.com",
    "pec": "tecnaria@pec.confindustriavicenza.it",
}

def deterministic_contacts_answer(q: str) -> str | None:
    """
    Risponde SOLO se la domanda riguarda contatti/indirizzo/telefono/email/PEC/sede.
    Regex con confini di parola per evitare falsi positivi (es. 'CFT').
    """
    ql = (q or "").lower()
    patterns = [
        r"\bcontatti?\b", r"\bcontatto\b", r"\bindirizz[io]\b", r"\bdove\s+si\s+trova\b",
        r"\bsede\b", r"\btelefono\b|\btel\.\b", r"\bcellulare\b|\bmobile\b",
        r"\bemail\b|\bmail\b", r"\bpec\b", r"\bfax\b",
        r"\bpartita\s*iva\b|\bp\.?\s*iva\b", r"\bcodice\s*fiscale\b"
    ]
    if any(re.search(p, ql) for p in patterns):
        c = TECNARIA_CONTACT
        return (
            f"**{c['ragione_sociale']} — Contatti ufficiali**\n"
            f"- **Indirizzo**: {c['indirizzo']}\n"
            f"- **Partita IVA / Codice Fiscale**: {c['piva_cf']}\n"
            f"- **Telefono**: {c['telefono']}\n"
            f"- **Fax**: {c['fax']}\n"
            f"- **Email**: {c['email']}\n"
            f"- **PEC**: {c['pec']}\n"
        )
    return None

# =============== Guard-rail & perimetro ===============
BANNED = [r"\bHBV\b", r"\bFVA\b", r"\bAvantravetto\b", r"\bT[\- ]?Connect\b", r"\bAlfa\b"]

SYSTEM_TEXT = (
    "Sei un esperto dei prodotti Tecnaria S.p.A. di Bassano del Grappa. "
    "Rispondi in modo completo, strutturato e operativo: titolo breve + punti tecnici, con esempi pratici e indicazioni di posa. "
    "Includi, se utile, avvertenze e tolleranze. Non inventare dati: se servono parametri di progetto, spiega cosa chiedere al cliente. "
    "Resta nel perimetro Tecnaria (connettori CTF/CTL, CEM-E, MINI CEM-E, V-CEM-E, CTCEM, Diapason, Omega, GTS; Spit P560; "
    "certificazioni, manuali di posa, capitolati, computi). Se la domanda non è su prodotti Tecnaria, di' che non puoi. "
    "Rispondi nella stessa lingua dell’utente (Italiano o Inglese)."
)

TOPIC_KEYS = {
    "CTF": ["ctf","cft","acciaio-calcestruzzo","lamiera","grecata"],
    "CTL": ["ctl","legno-calcestruzzo","legno","solaio in legno","timber","wood-concrete"],
    "CEM-E": ["cem-e","ripresa di getto","nuovo su esistente","cucitura","joint","construction joint"],
    "MINI CEM-E": ["mini cem-e","mini cem","mini"],
    "V-CEM-E": ["v-cem-e","vcem","v cem","verticale"],
    "CTCEM": ["ctcem","ct cem"],
    "DIAPASON": ["diapason","connettore diapason"],
    "OMEGA": ["omega","connettore omega"],
    "GTS": ["manicotto gts","gts","giunto trave-colonna","sleeve"],
    "P560": ["p560","spit p560","chiodatrice","nailer","powder-actuated"]
}

def banned(text: str) -> bool:
    q = (text or "").lower()
    for keys in TOPIC_KEYS.values():
        if any(k in q for k in keys):
            return False
    return any(re.search(p, text, re.IGNORECASE) for p in BANNED)

# =============== Stili A/B/C ===============
STYLE_HINTS = {
    "A": "Formato: 2–3 bullet essenziali.",
    "B": "Formato: Titolo + 3–4 bullet tecnici + chiusura breve.",
    "C": "Formato: Titolo + 5–8 punti tecnici + suggerimento operativo.",
}
STYLE_TOKENS = {"A": 250, "B": 450, "C": 700}
def normalize_style(val):
    if not val: return "B"
    v = str(val).strip().upper()
    return "A" if v in ("A","SHORT") else "C" if v in ("C","DETAILED","LONG") else "B"

# =============== Rilevamento lingua + Traduzione note ===============
EN_WORDS = set("""
the and for with from into between against without above below during before after within beyond
what which who where when how why can could should would may might will shall do does did is are was were be been being
use height heights slab deck concrete steel wood beam joist rebar cover shear connector technical note example
""".split())
IT_WORDS = set("""
il lo la i gli le un una per con da tra fra senza sopra sotto durante prima dopo entro oltre
che quale chi dove quando come perché posso potrei dovrei ma forse sarà fare fa è sono era erano essere stato
altezza altezze soletta calcestruzzo acciaio legno trave solaio copriferro connettore nota tecnica esempio
""".split())

def detect_lang(text: str) -> str:
    t = (text or "").lower()
    en = sum(1 for w in re.findall(r"[a-z]+", t) if w in EN_WORDS)
    it = sum(1 for w in re.findall(r"[a-zàèéìòóù]+", t) if w in IT_WORDS)
    if re.search(r"[àèéìòóù]", t): it += 2
    if "ctf" in t or "copriferro" in t or "soletta" in t: it += 1
    return "en" if en > it else "it"

def translate_text(note_text: str, target_lang: str) -> str:
    """
    Traduci la nota (solo verso EN per ora). Mantieni titoli/markdown.
    """
    if target_lang != "en":
        return note_text
    sys = "You are a professional technical translator. Translate into natural, precise ENGLISH. Keep headings and bullet lists. Do not add commentary."
    user = f"Translate this technical note into English. Keep structure:\n\n{note_text}"
    try:
        if NEW_SDK:
            resp = client.responses.create(
                model=OPENAI_MODEL,
                input=[{"role":"system","content":sys},{"role":"user","content":user}],
                top_p=1, max_output_tokens=800
            )
            out = getattr(resp, "output_text", None)
            return out.strip() if out else note_text
        else:
            resp = openai.ChatCompletion.create(
                model=OPENAI_MODEL,
                messages=[{"role":"system","content":sys},{"role":"user","content":user}],
                top_p=1, max_tokens=800
            )
            return (resp["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return note_text

# =============== NOTE TECNICHE: fallback embedded ===============
EMBEDDED_NOTES = {
    "CTF": [
        """Nota tecnica CTF – Altezza, soletta e copriferro
- Considerare: spessore soletta, copriferro minimo, tipo di lamiera grecata (se presente).
Esempio:
- Soletta 60 mm → connettore CTF090
- Soletta 80 mm con copriferro 25 mm → connettore CTF105"""
    ],
    "CTL": [
        """Nota tecnica CTL – Legno-calcestruzzo
- Verificare classe del legno (umidità, difetti, resistenza).
- Preforo secondo manuale, attenzione a fessurazioni.
- Copriferro minimo nel getto collaborante (≥ 25 mm salvo prescrizioni).
- Passo e densità connettori in funzione del calcolo a taglio.
Esempio: tavolato 50 mm + getto 60–80 mm → connettore CTL medio, passo 15–20 cm."""
    ],
    "CEM-E": [
        """Nota tecnica CEM-E – Ripresa di getto
- Pulizia, scabrosità e saturazione del cls esistente.
- Primer/boiacca adesiva se previsto da specifica.
- Connettori a ponte per trasferimento taglio lungo giunto.
- Copriferro e ricoprimento ancoraggi secondo normativa."""
    ],
    "DIAPASON": [
        """Nota tecnica DIAPASON – Rinforzo su lamiere grecate
- Studiare passo e orientamento lamiera.
- Verificare interferenze con armature e impianti.
- Predisporre guida di posa per allineamento connettori.
- Tolleranze: verticalità ±2 mm, passo ±5 mm su 1 m."""
    ],
    "OMEGA": [
        """Nota tecnica OMEGA – Collegamenti su profili sottili
- Idoneo per lamiere sottili; attenzione a schiacciamento locale.
- Verificare coppia serraggio e rondelle adeguate.
- Protezione anticorrosiva se in ambienti aggressivi."""
    ],
    "GTS": [
        """Nota tecnica GTS – Manicotto filettato
- Controllare lunghezza d’innesto e filettatura compatibile.
- Pulizia filetti e coppia di serraggio con dinamometrica.
- Certificazioni per uso strutturale secondo specifica."""
    ],
    "P560": [
        """Nota tecnica SPIT P560 – Chiodatrice a sparo
- Usare DPI e seguire il manuale di sicurezza.
- Selezione chiodo e carica in funzione del supporto (acciaio/lamiere).
- Prova preliminare di tenuta e profondità di infissione.
- Manutenzione: pulizia camera e guide, controllo otturatore."""
    ],
}

# =============== Utility note ===============
def guess_topic(question: str) -> str | None:
    q = (question or "").lower()
    for topic, keys in TOPIC_KEYS.items():
        if any(k in q for k in keys): return topic
    return None

def load_note_files(topic: str):
    folder = os.path.join(NOTE_DIR, topic)
    return sorted(glob.glob(os.path.join(folder, "*.txt")))

def best_local_note(question: str, topic: str):
    """
    Ritorna (testo_nota, fonte_str)
    1) match fuzzy su .txt locali  2) fallback embedded  3) None
    """
    paths = load_note_files(topic)
    q = (question or "").lower()

    # 1) file locali
    if paths:
        best_score, best_text, best_src = -1, None, None
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    txt = f.read().strip()
            except Exception:
                continue
            blob = (os.path.basename(p) + "\n" + txt).lower()
            score = fuzz.token_set_ratio(q, blob)
            for k in ("altezza","altezze","soletta","copriferro","ctf","ctl","cem-e","diapason","omega","gts","p560"):
                if k in blob or k in q: score += 5
            if score > best_score:
                best_score, best_text, best_src = score, txt, f"{NOTE_DIR}/{topic}/{os.path.basename(p)}"
        if best_text:
            return best_text, best_src

    # 2) embedded
    notes = EMBEDDED_NOTES.get(topic, [])
    if notes:
        return notes[0].strip(), f"embedded:{topic}"

    # 3) nulla
    return None, None

def attach_local_note(answer: str, question: str) -> str:
    """
    Aggancia SEMPRE una nota se esiste almeno una fonte (file locale o embedded).
    Se la domanda è in inglese, traduce automaticamente la nota in inglese.
    """
    topic = guess_topic(question)
    if not topic:
        return answer
    note, src = best_local_note(question, topic)
    if not note:
        return answer

    lang = detect_lang(question)
    shown_note = translate_text(note, "en" if lang == "en" else "it")

    lines = shown_note.splitlines()
    if lines and len(lines[0]) <= 100:
        title = lines[0].strip()
        body  = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        if lang == "en":
            block = f"---\n📎 Technical note (local) — {title}\n{body}" if body else f"---\n📎 Technical note (local)\n{title}"
        else:
            block = f"---\n📎 Nota tecnica (locale) — {title}\n{body}" if body else f"---\n📎 Nota tecnica (locale)\n{title}"
    else:
        block = f"---\n📎 Technical note (local)\n{shown_note}" if lang == "en" \
                else f"---\n📎 Nota tecnica (locale)\n{shown_note}"

    if src:
        if src.startswith("embedded:"):
            block += f"\n_(source: {src})_" if lang=="en" else f"\n_(fonte: {src})_"
        else:
            rel = os.path.relpath(src, start=NOTE_DIR)
            block += f"\n_(source: {rel})_" if lang=="en" else f"\n_(fonte: {rel})_"

    return (answer or "").rstrip() + "\n\n" + block

# =============== DETERMINISTICO: CODICI CTF ===============
CTF_CODES = [
    ("CTF020", 20), ("CTF025", 25), ("CTF030", 30), ("CTF040", 40),
    ("CTF060", 60), ("CTF070", 70), ("CTF080", 80), ("CTF090", 90),
    ("CTF105",105), ("CTF125",125), ("CTF135",135),
]
def deterministic_ctf_codes_answer(q: str) -> str | None:
    ql = (q or "").lower()
    if not (("ctf" in ql or "cft" in ql) and any(k in ql for k in ["codici","codice","lista","listino","catalogo","codes","list"])):
        return None
    if detect_lang(q) == "en":
        lines = ["**CTF Series — Shank heights (mm)**"]
        for code,h in CTF_CODES: lines.append(f"- {code} — {h} mm")
        lines.append("\nFor proper use, check slab thickness/cover and the Tecnaria installation manual.")
    else:
        lines = ["**Serie CTF — Altezze gambo (mm)**"]
        for code,h in CTF_CODES: lines.append(f"- {code} — {h} mm")
        lines.append("\nPer l’impiego corretto verificare spessore soletta/coprif. e manuale di posa Tecnaria.")
    return "\n".join(lines)

# =============== DETERMINISTICO: ALTEZZA CTF da TXT ===============
def _extract_mm(text: str, key: str) -> list[int]:
    t = text.lower(); out = []
    patt = rf"{key}\s*[:=]?\s*(\d{{2,3}})\s*(?:mm|m\s*m)?"
    for m in re.finditer(patt, t):
        try: out.append(int(m.group(1)))
        except: pass
    return out

def _find_ctf_code_in_line(line: str) -> str | None:
    m = re.search(r"\bCTF\s*0?(\d{2,3})\b", line, re.IGNORECASE)
    return "CTF"+m.group(1).zfill(3) if m else None

def _deterministic_from_note(soletta_mm: int, copriferro_mm: int) -> str | None:
    paths = load_note_files("CTF")
    if not paths: return None
    s_str, c_str = str(soletta_mm), str(copriferro_mm)

    # entrambi i numeri
    for p in paths:
        try:
            with open(p,"r",encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if s_str in ln and c_str in ln:
                        code = _find_ctf_code_in_line(ln)
                        if code: return code
        except Exception:
            continue
    # solo soletta
    for p in paths:
        try:
            with open(p,"r",encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if s_str in ln:
                        code = _find_ctf_code_in_line(ln)
                        if code: return code
        except Exception:
            continue
    # solo copriferro
    for p in paths:
        try:
            with open(p,"r",encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if c_str in ln:
                        code = _find_ctf_code_in_line(ln)
                        if code: return code
        except Exception:
            continue
    return None

def deterministic_ctf_height_answer(question: str):
    """
    Ritorna (answer_text, matched_bool):
      - matched_bool=True  -> trovato codice nel TXT (risposta certa)
      - matched_bool=False -> NON trovato -> farà LLM + nota a valle
    """
    q = (question or "").lower()
    if not (("ctf" in q or "cft" in q) and ("altezza" in q or "altezze" in q or "height" in q or "heights" in q)):
        return None, False
    so = _extract_mm(q, r"soletta|slab|deck|thickness")
    co = _extract_mm(q, r"copriferro|cover|rebar\s*cover|concrete\s*cover")
    if not so:
        return None, False
    soletta = so[0]; copri = co[0] if co else 25
    code = _deterministic_from_note(soletta, copri)
    if not code:
        base_it = f"**Dati ricevuti**: soletta **{soletta} mm**, copriferro **{copri} mm**."
        base_en = f"**Input received**: slab **{soletta} mm**, rebar cover **{copri} mm**."
        return (base_en if detect_lang(question)=="en" else base_it), False
    if detect_lang(question) == "en":
        text = (
            f"**Recommended CTF height: {code}**\n"
            f"- Inputs: slab **{soletta} mm**, rebar cover **{copri} mm**.\n"
            f"- Mapping retrieved from internal CTF notes (*.txt).\n"
            f"If you want, I can also check spacing, density and MEP interferences."
        )
    else:
        text = (
            f"**Altezza consigliata CTF: {code}**\n"
            f"- Dati ricevuti: soletta **{soletta} mm**, copriferro **{copri} mm**.\n"
            f"- Abbinamento ricavato da note interne CTF (*.txt).\n"
            f"Se vuoi verifico anche passo, densità e interferenze impianti."
        )
    return text, True

# =============== DETERMINISTICI CTL & altre famiglie ===============
CTL_CODES = [
    # Esempio indicativo: sostituisci con lista ufficiale quando disponibile
    ("CTL060", 60), ("CTL080", 80), ("CTL100", 100), ("CTL120", 120)
]
def deterministic_ctl_codes_answer(q: str) -> str | None:
    ql = (q or "").lower()
    if not (("ctl" in ql) and any(k in ql for k in ["codici","codice","lista","listino","catalogo","codes","list"])):
        return None
    if detect_lang(q) == "en":
        lines = ["**CTL Series — Shank heights (mm)**"]
        for code,h in CTL_CODES: lines.append(f"- {code} — {h} mm")
        lines.append("\nFor proper use, check timber class, slab thickness/cover and Tecnaria installation manual.")
    else:
        lines = ["**Serie CTL — Altezze gambo (mm)**"]
        for code,h in CTL_CODES: lines.append(f"- {code} — {h} mm")
        lines.append("\nPer l’impiego corretto verificare classe del legno, spessore soletta/coprif. e manuale di posa Tecnaria.")
    return "\n".join(lines)

def deterministic_ctl_height_answer(question: str):
    q = (question or "").lower()
    if not ("ctl" in q and ("altezza" in q or "altezze" in q or "height" in q or "heights" in q)):
        return None, False
    so = _extract_mm(q, r"soletta|slab|deck|thickness")
    if not so: return None, False
    soletta = so[0]
    paths = load_note_files("CTL")
    if paths:
        s_str = str(soletta)
        for p in paths:
            try:
                with open(p,"r",encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if s_str in ln:
                            m = re.search(r"\bCTL\s*0?(\d{2,3})\b", ln, re.IGNORECASE)
                            if m:
                                code = "CTL"+m.group(1).zfill(3)
                                if detect_lang(question) == "en":
                                    text = (f"**Recommended CTL height: {code}**\n"
                                            f"- Input: slab **{soletta} mm**.\n"
                                            f"- Mapping retrieved from internal CTL notes (*.txt).")
                                else:
                                    text = (f"**Altezza consigliata CTL: {code}**\n"
                                            f"- Dato ricevuto: soletta **{soletta} mm**.\n"
                                            f"- Abbinamento da note interne CTL (*.txt).")
                                return text, True
            except Exception:
                continue
    base = f"**Dati ricevuti**: soletta **{soletta} mm**." if detect_lang(question)=="it" else f"**Input received**: slab **{soletta} mm**."
    return base, False

def deterministic_cem_e_variants_answer(q: str) -> str | None:
    ql = (q or "").lower()
    trigger = any(k in ql for k in ["cem-e","mini cem-e","v-cem-e","ctcem"])
    ask_codes = any(k in ql for k in ["codici","codice","varianti","versioni","models","variants","types"])
    if not (trigger and ask_codes): return None
    if detect_lang(q) == "en":
        return (
            "**CEM-E family — Variants**\n"
            "- **CEM-E**: shear connectors for construction joints (new-to-existing concrete).\n"
            "- **MINI CEM-E**: compact version for reduced thicknesses and tight spaces.\n"
            "- **V-CEM-E**: vertical connectors for vertical joints/couplings.\n"
            "- **CTCEM**: specific configuration for particular structural constraints.\n"
            "\nUse case depends on joint geometry, thickness, cover and required shear transfer."
        )
    else:
        return (
            "**Famiglia CEM-E — Varianti**\n"
            "- **CEM-E**: connettori a taglio per riprese di getto (nuovo su esistente).\n"
            "- **MINI CEM-E**: versione compatta per spessori ridotti e spazi contenuti.\n"
            "- **V-CEM-E**: connettori verticali per giunti verticali/accoppiamenti.\n"
            "- **CTCEM**: configurazione specifica per vincoli particolari.\n"
            "\nLa scelta dipende da geometria del giunto, spessori, copriferro e taglio da trasferire."
        )

def deterministic_other_families_answer(q: str) -> str | None:
    ql = (q or "").lower()
    if "diapason" in ql:
        return ("**Diapason — Connettori per lamiera grecata**\n"
                "- Ottimizzati per solai collaboranti con lamiere grecate.\n"
                "- Verificare passo lamiera, interferenze e tolleranze di posa.\n"
                "- Consultare nota tecnica e manuale di posa Tecnaria.") if detect_lang(q)!="en" else (
                "**Diapason — Connectors for trapezoidal deck**\n"
                "- Optimized for composite slabs with trapezoidal steel deck.\n"
                "- Check deck pitch, clashes and installation tolerances.\n"
                "- Refer to Tecnaria technical note and installation manual.")
    if "omega" in ql:
        return ("**Omega — Collegamenti su lamiere sottili**\n"
                "- Idonei a profili sottili; attenzione a schiacciamenti locali.\n"
                "- Coppia di serraggio e rondelle adeguate.\n"
                "- Vedi nota tecnica dedicata.") if detect_lang(q)!="en" else (
                "**Omega — Connections on thin sheets**\n"
                "- Suitable for thin profiles; watch local crushing.\n"
                "- Proper torque and washers.\n"
                "- See dedicated technical note.")
    if "gts" in ql:
        return ("**GTS — Manicotti filettati**\n"
                "- Verifica lunghezza d’innesto e compatibilità filetti.\n"
                "- Pulizia e coppia con dinamometrica.\n"
                "- Certificazioni per uso strutturale.") if detect_lang(q)!="en" else (
                "**GTS — Threaded sleeves**\n"
                "- Check engagement length and thread compatibility.\n"
                "- Clean threads and torque with torque wrench.\n"
                "- Structural-use certifications.")
    return None

def deterministic_p560_answer(q: str) -> str | None:
    ql = (q or "").lower()
    if not any(k in ql for k in ["p560","spit p560","chiodatrice","nailer","powder-actuated"]):
        return None
    if detect_lang(q) == "en":
        return (
            "**SPIT P560 — Powder-actuated tool**\n"
            "- **Safety**: PPE, training, follow the manual strictly.\n"
            "- **Fasteners/charges**: select by base material (steel, deck thickness).\n"
            "- **Test**: preliminary pull-out / penetration checks.\n"
            "- **Maintenance**: clean chamber and guides; check piston/stopper."
        )
    else:
        return (
            "**SPIT P560 — Chiodatrice a sparo**\n"
            "- **Sicurezza**: DPI, formazione, rispetto del manuale d’uso.\n"
            "- **Chiodi/cariche**: selezione in funzione del supporto (acciaio, spessore lamiera).\n"
            "- **Prova**: verifica preliminare di tenuta/profondità.\n"
            "- **Manutenzione**: pulizia camera e guide; controllo otturatore."
        )

# =============== OpenAI helpers ===============
def ask_new_sdk(system_text: str, user_text: str, style_tokens: int, temperature: float) -> str:
    params = {
        "model": OPENAI_MODEL,
        "input": [{"role":"system","content":system_text},{"role":"user","content":user_text}],
        "top_p": 1, "max_output_tokens": style_tokens
    }
    if temperature and temperature > 0: params["temperature"] = temperature
    resp = client.responses.create(**params)  # type: ignore
    text = getattr(resp, "output_text", None)
    if text: return text.strip()
    out = getattr(resp, "output", None) or []
    parts = []
    for item in out:
        if getattr(item,"type","")=="message":
            for c in getattr(item,"content",[]) or []:
                if getattr(c,"type","")=="output_text":
                    t = getattr(c,"text","") or ""
                    if t: parts.append(t)
    return "".join(parts).strip()

def ask_legacy_sdk(system_text: str, user_text: str, style_tokens: int, temperature: float) -> str:
    kwargs = dict(model=OPENAI_MODEL,
                  messages=[{"role":"system","content":system_text},{"role":"user","content":user_text}],
                  top_p=1, max_tokens=style_tokens)
    if temperature and temperature > 0: kwargs["temperature"] = temperature
    resp = openai.ChatCompletion.create(**kwargs)  # type: ignore
    return (resp["choices"][0]["message"]["content"] or "").strip()

def call_model(question: str, style: str) -> str:
    toks = STYLE_TOKENS.get(style, 450)
    prompt = f"Domanda utente: {question}\n\n{STYLE_HINTS.get(style,'')}"
    if NEW_SDK:
        out = ask_new_sdk(SYSTEM_TEXT, prompt, toks, TEMPERATURE)
        if not out: out = ask_new_sdk(SYSTEM_TEXT, question, toks, TEMPERATURE)
        return out
    else:
        out = ask_legacy_sdk(SYSTEM_TEXT, prompt, toks, TEMPERATURE)
        if not out: out = ask_legacy_sdk(SYSTEM_TEXT, question, toks, TEMPERATURE)
        return out

# =============== Routes ===============
@app.get("/")
def root_redirect(): return redirect("/ui", code=302)

@app.get("/status")
def status():
    return jsonify({
        "status":"ok", "service":"Tecnaria QA",
        "note_dir_exists": os.path.isdir(NOTE_DIR),
        "note_dir": NOTE_DIR,
        "endpoints": {"ask":"POST /ask {question, style? 'A'|'B'|'C'}", "ui":"GET /ui", "debug/notes":"GET /debug/notes"},
        "model": OPENAI_MODEL, "temperature": TEMPERATURE,
        "sdk": "new" if NEW_SDK else "legacy"
    }), 200

@app.get("/debug/notes")
def debug_notes():
    out = {"NOTE_DIR": NOTE_DIR, "exists": os.path.isdir(NOTE_DIR), "topics": {}}
    for topic in TOPIC_KEYS.keys():
        folder = os.path.join(NOTE_DIR, topic)
        files = sorted(glob.glob(os.path.join(folder, "*.txt")))
        out["topics"][topic] = {"folder": folder, "exists": os.path.isdir(folder), "files": files}
    return jsonify(out), 200

@app.post("/ask")
def ask():
    if not OPENAI_API_KEY:
        return jsonify({"error":"OPENAI_API_KEY non configurata"}), 500
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict): return jsonify({"error":"Body JSON non valido."}), 400

    q = (data.get("question") or "").strip()
    style = normalize_style(data.get("style"))
    if not q: return jsonify({"error":"Missing 'question'."}), 400

    # 1) Contatti (deterministico, no nota)
    c_ans = deterministic_contacts_answer(q)
    if c_ans:
        return jsonify({"answer": c_ans, "style_used":"D", "source":"deterministic_contacts"}), 200

    # Guardrail non-Tecnaria (se la domanda non contiene alcun topic Tecnaria noto)
    if banned(q):
        return jsonify({"answer":"Non posso rispondere: non è un prodotto Tecnaria ufficiale.", "source":"guardrail"}), 200

    # 2) Codici CTF (deterministico) + nota
    cod_ans = deterministic_ctf_codes_answer(q)
    if cod_ans:
        cod_ans = attach_local_note(cod_ans, q)
        return jsonify({"answer": cod_ans, "style_used":"D", "source":"deterministic_ctf_codes"}), 200

    # 2b) CTL — codici (deterministico) + nota
    ctl_codes = deterministic_ctl_codes_answer(q)
    if ctl_codes:
        ctl_codes = attach_local_note(ctl_codes, q)
        return jsonify({"answer": ctl_codes, "style_used":"D", "source":"deterministic_ctl_codes"}), 200

    # 3) Altezza CTF
    det_ans, matched = deterministic_ctf_height_answer(q)
    if det_ans and matched:
        det_ans = attach_local_note(det_ans, q)
        return jsonify({"answer": det_ans, "style_used":"D", "source":"deterministic_ctf_height"}), 200
    elif det_ans and not matched:
        try:
            llm = call_model(q, style)
            if not llm: llm = det_ans
            llm = attach_local_note(llm, q)
            return jsonify({"answer": llm, "style_used": style, "source":"llm_fallback_with_note"}), 200
        except Exception:
            det_ans = attach_local_note(det_ans, q)
            return jsonify({"answer": det_ans, "style_used":"D", "source":"deterministic_ctf_height_fallback"}), 200

    # 3b) Altezza CTL
    ctl_ans, ctl_match = deterministic_ctl_height_answer(q)
    if ctl_ans and ctl_match:
        ctl_ans = attach_local_note(ctl_ans, q)
        return jsonify({"answer": ctl_ans, "style_used":"D", "source":"deterministic_ctl_height"}), 200
    elif ctl_ans and not ctl_match:
        try:
            llm = call_model(q, style)
            if not llm: llm = ctl_ans
            llm = attach_local_note(llm, q)
            return jsonify({"answer": llm, "style_used": style, "source":"llm_fallback_with_note_ctl"}), 200
        except Exception:
            ctl_ans = attach_local_note(ctl_ans, q)
            return jsonify({"answer": ctl_ans, "style_used":"D", "source":"deterministic_ctl_height_fallback"}), 200

    # 4) Varianti CEM-E (descrittivo deterministico) + nota
    cemv = deterministic_cem_e_variants_answer(q)
    if cemv:
        cemv = attach_local_note(cemv, q)
        return jsonify({"answer": cemv, "style_used":"D", "source":"deterministic_cem_e_variants"}), 200

    # 5) Diapason/Omega/GTS (descrittivi) + nota
    other = deterministic_other_families_answer(q)
    if other:
        other = attach_local_note(other, q)
        return jsonify({"answer": other, "style_used":"D", "source":"deterministic_other_families"}), 200

    # 6) SPIT P560 (deterministico) + nota
    p560 = deterministic_p560_answer(q)
    if p560:
        p560 = attach_local_note(p560, q)
        return jsonify({"answer": p560, "style_used":"D", "source":"deterministic_p560"}), 200

    # 7) LLM generale + nota
    try:
        ans = call_model(q, style)
        if not ans: ans = "Non ho ricevuto testo dal modello in questa richiesta."
        if banned(ans): ans = "Non posso rispondere: non è un prodotto Tecnaria ufficiale."
        ans = attach_local_note(ans, q)
        return jsonify({"answer": ans, "style_used": style, "source":"openai_new" if NEW_SDK else "openai_legacy"}), 200
    except Exception as e:
        logging.exception("Errore OpenAI")
        return jsonify({"error": f"OpenAI error: {str(e)}"}), 500

# =============== UI ===============
HTML_UI = """<!doctype html>
<html lang="it"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tecnaria QA Bot</title>
<style>
:root{--bg:#0f172a;--card:#111827;--ink:#e5e7eb;--muted:#9ca3af;--accent:#22d3ee}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,Segoe UI,Roboto,Arial}
.wrap{max-width:900px;margin:40px auto;padding:0 16px}
.card{background:var(--card);border:1px solid #1f2937;border-radius:16px;padding:20px}
h1{margin:0 0 8px;font-size:22px}
.sub{color:var(--muted);font-size:14px;margin-bottom:16px}
textarea{width:100%;min-height:110px;border-radius:12px;border:1px solid #374151;background:#0b1220;color:var(--ink);padding:12px}
.btn{background:var(--accent);color:#041014;border:0;border-radius:12px;padding:12px 16px;font-weight:700;cursor:pointer;margin-top:10px}
.out{white-space:pre-wrap;background:#0b1220;border:1px solid #1f2937;border-radius:12px;padding:14px;margin-top:16px}
label{display:inline-block;margin:8px 12px 0 0}
</style></head>
<body><div class="wrap"><div class="card">
<h1>Tecnaria QA Bot</h1>
<div class="sub">Domande libere su Tecnaria. Se esiste una nota locale, la vedi in fondo.</div>
<textarea id="question" placeholder="Es.: Dammi i codici CTF — Oppure: altezza CTF con soletta 80 e copriferro 25. Ask in English to get the answer + note in English."></textarea>
<div>
<label><input type="radio" name="style" value="A"> A — Breve</label>
<label><input type="radio" name="style" value="B" checked> B — Standard</label>
<label><input type="radio" name="style" value="C"> C — Dettagliata</label>
</div>
<button class="btn" onclick="ask()">Chiedi</button>
<div id="output" class="out" style="display:none"></div>
<div id="err" class="out" style="display:none; border-color:#7f1d1d; background:#450a0a; color:#fecaca"></div>
<div class="sub" id="meta"></div>
</div></div>
<script>
async function ask(){
  const q=document.getElementById('question').value;
  const style=document.querySelector('input[name="style"]:checked').value;
  const out=document.getElementById('output'), err=document.getElementById('err');
  out.style.display='none'; err.style.display='none';
  try{
    const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question:q, style})});
    const j=await r.json();
    if(!r.ok||j.error){ err.textContent=j.error||('HTTP '+r.status); err.style.display='block'; }
    else { out.textContent=j.answer||'(nessuna risposta)'; out.style.display='block'; }
  }catch(e){ err.textContent='Errore di rete: '+e.message; err.style.display='block'; }
  try{
    const s=await fetch('/status',{cache:'no-store'}); const sj=await s.json();
    document.getElementById('meta').textContent =
      `Model: ${sj.model} • Temp: ${sj.temperature} • SDK: ${sj.sdk} • Note dir: ${sj.note_dir} (exists: ${sj.note_dir_exists})`;
  }catch(e){}
}
</script></body></html>"""
@app.get("/ui")
def ui(): return Response(HTML_UI, mimetype="text/html")

# =============== Error handling ===============
@app.errorhandler(HTTPException)
def _http(e: HTTPException): return jsonify({"error": e.description, "code": e.code}), e.code

@app.errorhandler(Exception)
def _any(e: Exception):
    logging.exception("Errore imprevisto"); return jsonify({"error": str(e)}), 500

# =============== Local run ===============
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
