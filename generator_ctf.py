# generator_ctf.py
import json
from itertools import product
from uuid import uuid4

families = []
models = [("CTF020",20),("CTF040",40),("CTF060",60),("CTF080",80)]
materials = ["S235","S275","S355"]
laminas = ["no lamiera","1x1.5 mm","2x1.0 mm"]
questions = []

# templates
q_templates = [
    "Quali sono i codici e modelli dei connettori CTF?",
    "Come si posa un {model} su trave in acciaio {material} con {lamina}?",
    "Come tarare P560 per posa di {model} su {material}?",
    "Come verifico in cantiere che i chiodi HSBR14 di {model} sono entrati correttamente?",
    "Quali errori evitare nella posa di {model} su {lamina}?"
]

answers = {
    "code_list": lambda: ("**CTF – Codici e modelli**\n\n" +
                         "\n".join([f"- {m[0]}: altezza {m[1]} mm; 2 chiodi HSBR14" for m in models]) +
                         "\n\nUso: SPIT P560 + kit Tecnaria; nessuna resina."),
    "posa": lambda model, material, lamina: (f"Posa del {model} su trave {material} con {lamina}:\n"
                                            "1) Tracciare maglia; 2) pulire; 3) posare piastra; 4) doppia chiodatura P560; "
                                            "5) verificare piastra aderente; 6) registrare parametri."),
    "taratura": lambda model, material, lamina: ("Eseguire 2-3 tiri di prova sul medesimo acciaio; verificare sporgenza <1mm; "
                                                "annotare potenza e lotto."),
    "verifica": lambda model, material, lamina: ("Controllo visivo e campione: foto, prova di trazione su 10-15 pezzi; "
                                                 "se >5% non conforme, ritarare."),
    "errori": lambda model, material, lamina: ("Errori comuni: potenza insufficiente, lamiera non serrata, connettore disassato, colpo a vuoto.")
}

out = []

# Add base codici entry
out.append({
    "id": "CTF-CODICI-001",
    "family": "CTF",
    "question": "Mi dici i codici dei connettori CTF?",
    "answer": answers["code_list"](),
    "tags": ["CTF","codici","catalogo"],
    "source": "gold"
})

# generate combinations
ctr = 2
for model,material,lamina in product([m[0] for m in models], materials, laminas):
    # pose question
    out.append({
        "id": f"CTF-{ctr:04d}",
        "family": "CTF",
        "question": q_templates[1].format(model=model, material=material, lamina=lamina),
        "answer": answers["posa"](model, material, lamina),
        "tags": ["CTF","posa",model,material,lamina],
        "source": "gold"
    })
    ctr += 1

    out.append({
        "id": f"CTF-{ctr:04d}",
        "family": "CTF",
        "question": q_templates[2].format(model=model, material=material, lamina=lamina),
        "answer": answers["taratura"](model, material, lamina),
        "tags": ["P560","taratura",model],
        "source": "gold"
    })
    ctr += 1

    out.append({
        "id": f"CTF-{ctr:04d}",
        "family": "CTF",
        "question": q_templates[3].format(model=model, material=material, lamina=lamina),
        "answer": answers["verifica"](model, material, lamina),
        "tags": ["verifica",model],
        "source": "gold"
    })
    ctr += 1

    out.append({
        "id": f"CTF-{ctr:04d}",
        "family": "CTF",
        "question": q_templates[4].format(model=model, material=material, lamina=lamina),
        "answer": answers["errori"](model, material, lamina),
        "tags": ["errori",model],
        "source": "gold"
    })
    ctr += 1

# Save
with open("ctf_gold_generated.json","w",encoding="utf-8") as f:
    json.dump(out,f,ensure_ascii=False,indent=2)

print("Generated", len(out), "items -> ctf_gold_generated.json")
