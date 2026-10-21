# -*- coding: utf-8 -*-
"""Le due polizze di PROVA (compagnie inesistenti) per il collaudo del confronto.
Identiche a quelle dell'8 ottobre 2026. Ogni pagina e' marcata come documento fittizio.
Uso: genera(cartella) -> (percorso A, percorso B)."""
import os
import sys
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak)

ss = getSampleStyleSheet()
H1 = ParagraphStyle('H1', parent=ss['Heading1'], fontSize=15, spaceAfter=6)
H2 = ParagraphStyle('H2', parent=ss['Heading2'], fontSize=11, spaceBefore=10, spaceAfter=3)
BODY = ParagraphStyle('BODY', parent=ss['Normal'], fontSize=9, leading=12, spaceAfter=4)
SMALL = ParagraphStyle('SMALL', parent=ss['Normal'], fontSize=7.5, leading=9,
                       textColor=colors.grey)


def footer(canvas, doc, compagnia):
    canvas.saveState()
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(colors.grey)
    canvas.drawString(20 * mm, 12 * mm,
                      "DOCUMENTO FITTIZIO generato per collaudo software - compagnia inesistente - non utilizzabile a fini contrattuali")
    canvas.drawRightString(190 * mm, 12 * mm, "%s - pag. %d" % (compagnia, doc.page))
    canvas.restoreState()


def tabella(dati, widths):
    t = Table(dati, colWidths=widths)
    t.setStyle(TableStyle([
        ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 8),
        ('FONT', (0, 1), (-1, -1), 'Helvetica', 8),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8E8E8')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return t


def doc_a(path):
    S = []
    S.append(Paragraph("VERSILIA VITA S.p.A.", H1))
    S.append(Paragraph("Set informativo - Contratto di assicurazione temporanea caso morte "
                       "a capitale costante<br/>Prodotto: VERSILIA PROTEZIONE 200 - Mod. VP-200/2026 - "
                       "Edizione in vigore dal 01.01.2026", BODY))
    S.append(Paragraph("Documento fittizio, prodotto per il collaudo di un software di confronto. "
                       "La compagnia qui indicata non esiste.", SMALL))
    S.append(Spacer(1, 8))

    S.append(Paragraph("PROPOSTA PERSONALIZZATA n. VP/2026/004871", H2))
    S.append(tabella([
        ["Dato", "Valore"],
        ["Contraente e assicurato", "Uomo, 50 anni compiuti, non fumatore"],
        ["Professione dichiarata", "Impiegato amministrativo - classe di rischio 1"],
        ["Capitale assicurato caso morte", "200.000,00 EUR - costante per tutta la durata"],
        ["Durata del contratto", "15 anni"],
        ["Premio annuo lordo", "348,00 EUR"],
        ["Frazionamento ammesso", "annuale, semestrale, trimestrale, mensile - senza maggiorazione"],
        ["Accertamenti sanitari", "questionario anamnestico; visita medica non richiesta sotto 250.000 EUR"],
    ], [55 * mm, 110 * mm]))
    S.append(Spacer(1, 6))
    S.append(Paragraph("Il premio indicato e' comprensivo di caricamenti e imposte. Il contratto non prevede "
                       "valore di riscatto ne' di riduzione: in caso di interruzione del pagamento dei premi "
                       "la garanzia cessa senza alcun rimborso.", BODY))

    S.append(PageBreak())
    S.append(Paragraph("CONDIZIONI DI ASSICURAZIONE", H1))

    S.append(Paragraph("Art. 1 - Oggetto della garanzia principale", H2))
    S.append(Paragraph("La Compagnia si impegna a corrispondere ai beneficiari designati il capitale "
                       "assicurato indicato in proposta qualora il decesso dell'assicurato avvenga nel "
                       "corso della durata contrattuale, quale che ne sia la causa, con le sole "
                       "limitazioni previste agli articoli 3, 4 e 5.", BODY))

    S.append(Paragraph("Art. 2 - Decorrenza e periodo di carenza", H2))
    S.append(Paragraph("La garanzia decorre dalle ore 24 del giorno di pagamento della prima rata di premio. "
                       "Per i decessi dovuti a malattia la garanzia e' operante trascorsi <b>6 mesi</b> dalla "
                       "decorrenza. Il periodo di carenza non si applica qualora il decesso sia conseguenza "
                       "diretta di infortunio, di malattia infettiva acuta o di shock anafilattico.", BODY))
    S.append(Paragraph("La carenza e' inoltre esclusa qualora l'assicurato si sottoponga, a proprie spese, "
                       "a visita medica preventiva con esito favorevole.", BODY))

    S.append(Paragraph("Art. 3 - Rischi esclusi", H2))
    S.append(Paragraph("Sono esclusi dalla garanzia i decessi determinati da:", BODY))
    S.append(tabella([
        ["Causa esclusa", "Precisazione"],
        ["Partecipazione a competizioni motoristiche",
         "esclusa la partecipazione a gare, prove e allenamenti su circuito"],
        ["Paracadutismo e volo con mezzi non di linea", "compreso deltaplano e parapendio"],
        ["Alpinismo con scalata di roccia oltre il III grado", "e ascensioni oltre 4.000 metri"],
        ["Immersione subacquea con autorespiratore", "oltre i 40 metri di profondita'"],
        ["Atto doloso dell'assicurato o del beneficiario",
         "la garanzia resta operante verso gli altri beneficiari"],
        ["Guerra, insurrezione, eventi nucleari",
         "salvo che l'assicurato vi sia coinvolto suo malgrado nei primi 14 giorni"],
    ], [60 * mm, 105 * mm]))
    S.append(Spacer(1, 4))
    S.append(Paragraph("Art. 4 - Clausola di non contestabilita'", H2))
    S.append(Paragraph("Trascorsi 24 mesi dalla decorrenza, la Compagnia non puo' contestare le dichiarazioni "
                       "rese in sede di stipula, salvo il caso di dolo.", BODY))
    S.append(Paragraph("Art. 5 - Limitazione temporale di legge", H2))
    S.append(Paragraph("Il contratto non e' operante qualora il decesso avvenga entro <b>24 mesi</b> dalla "
                       "decorrenza per atto volontario dell'assicurato, secondo quanto previsto dalla "
                       "normativa applicabile.", BODY))

    S.append(PageBreak())
    S.append(Paragraph("GARANZIE COMPLEMENTARI", H1))
    S.append(tabella([
        ["Garanzia", "Operativita'", "Prestazione", "Premio"],
        ["Invalidita' permanente da\ninfortunio o malattia",
         "compresa nel premio\nbase",
         "anticipazione del capitale\nassicurato se il grado di\ninvalidita' riconosciuto e'\npari o superiore al 60%",
         "inclusa"],
        ["Doppio capitale per\ninfortunio",
         "compresa nel premio\nbase",
         "raddoppio del capitale, con\nil limite massimo di\n100.000 EUR di maggiorazione",
         "inclusa"],
        ["Inabilita' temporanea\ntotale",
         "opzionale, non\nattivata in proposta",
         "indennita' giornaliera di\n40 EUR, franchigia di\n30 giorni, massimo 365 gg",
         "72 EUR/anno"],
        ["Malattie gravi", "non prevista dal\nprodotto", "-", "-"],
    ], [38 * mm, 33 * mm, 62 * mm, 25 * mm]))

    S.append(Spacer(1, 8))
    S.append(Paragraph("SERVIZI ACCESSORI", H2))
    S.append(Paragraph("Il contratto comprende senza costi aggiuntivi: centrale operativa attiva 24 ore su 24 "
                       "per consulenza medica telefonica; invio di un medico in caso di urgenza nelle ore "
                       "notturne; rimpatrio sanitario dall'estero fino a 15.000 EUR.", BODY))

    S.append(Spacer(1, 10))
    S.append(Paragraph("CONDIZIONI ECONOMICHE E DIRITTI DEL CONTRAENTE", H2))
    S.append(tabella([
        ["Voce", "Valore"],
        ["Caricamenti sul premio", "22,0% del premio lordo"],
        ["Diritto di recesso", "30 giorni dalla conclusione del contratto"],
        ["Rimborso in caso di recesso", "premio versato al netto delle spese di emissione, pari a 25 EUR"],
        ["Sconto per adesione collettiva", "non previsto"],
        ["Rivalutazione del capitale", "non prevista - capitale costante"],
    ], [60 * mm, 105 * mm]))

    S.append(PageBreak())
    S.append(Paragraph("APPENDICE n. 1 - DEROGA ALLE CONDIZIONI DI ASSICURAZIONE", H1))
    S.append(Paragraph("La presente appendice forma parte integrante del contratto e, limitatamente a quanto "
                       "qui disciplinato, <b>prevale sulle condizioni di assicurazione</b> di cui al mod. "
                       "VP-200/2026.", BODY))
    S.append(Spacer(1, 6))
    S.append(Paragraph("1. In parziale deroga all'Art. 3, la pratica non agonistica di immersione subacquea "
                       "entro i 40 metri e dell'alpinismo su ghiacciaio con guida abilitata non costituisce "
                       "causa di esclusione.", BODY))
    S.append(Paragraph("2. In deroga all'Art. 2, il periodo di carenza per decesso da malattia e' ridotto a "
                       "<b>3 mesi</b> a fronte della consegna di referto ematochimico non anteriore a 90 giorni "
                       "con esito nella norma.", BODY))
    S.append(Paragraph("3. In deroga alle condizioni economiche, e' riconosciuto uno sconto commerciale del "
                       "5% sul premio annuo per i contraenti gia' titolari di altra polizza della Compagnia. "
                       "Lo sconto non e' stato applicato alla proposta n. VP/2026/004871.", BODY))
    S.append(Spacer(1, 6))
    S.append(Paragraph("Tutto quanto non espressamente derogato resta fermo e invariato.", BODY))

    SimpleDocTemplate(path, pagesize=A4, topMargin=18 * mm, bottomMargin=20 * mm,
                      leftMargin=20 * mm, rightMargin=20 * mm).build(
        S, onFirstPage=lambda c, d: footer(c, d, "VERSILIA VITA"),
        onLaterPages=lambda c, d: footer(c, d, "VERSILIA VITA"))


def doc_b(path):
    S = []
    S.append(Paragraph("NORDEST VITA S.p.A.", H1))
    S.append(Paragraph("Condizioni di assicurazione - Polizza temporanea caso morte "
                       "a capitale decrescente<br/>Prodotto: NE SERENA CASA E FAMIGLIA - "
                       "Mod. NES/04 - Edizione 03.2026", BODY))
    S.append(Paragraph("Documento fittizio, prodotto per il collaudo di un software di confronto. "
                       "La compagnia qui indicata non esiste.", SMALL))
    S.append(Spacer(1, 8))

    S.append(Paragraph("PREVENTIVO n. NE-88214/26", H2))
    S.append(tabella([
        ["Dato", "Valore"],
        ["Assicurato", "Uomo, 50 anni, non fumatore"],
        ["Capitale iniziale assicurato", "200.000,00 EUR"],
        ["Andamento del capitale",
         "decrescente in quote annue costanti fino a zero al termine\ndel 15. anno (capitale medio 100.000 EUR circa)"],
        ["Durata", "15 anni"],
        ["Premio annuo", "156,00 EUR"],
        ["Frazionamento", "annuale o semestrale; il frazionamento semestrale comporta\nuna maggiorazione del 3%"],
        ["Accertamenti sanitari", "questionario sanitario semplificato a 7 domande"],
    ], [55 * mm, 110 * mm]))

    S.append(PageBreak())
    S.append(Paragraph("CONDIZIONI DI ASSICURAZIONE", H1))

    S.append(Paragraph("Art. 1 - Prestazione assicurata", H2))
    S.append(Paragraph("In caso di decesso dell'assicurato durante il periodo di efficacia del contratto, la "
                       "Societa' liquida ai beneficiari il capitale residuo alla data del decesso, determinato "
                       "secondo il piano di decrescenza allegato al contratto.", BODY))

    S.append(Paragraph("Art. 2 - Efficacia della garanzia e carenza", H2))
    S.append(Paragraph("La garanzia ha effetto dalle ore 24 del giorno indicato in polizza. Per i decessi "
                       "conseguenti a malattia la garanzia opera decorsi <b>12 mesi</b> dalla data di effetto. "
                       "La carenza non opera nei casi di decesso da infortunio.", BODY))
    S.append(Paragraph("Non e' prevista alcuna modalita' di riduzione o eliminazione del periodo di carenza.", BODY))

    S.append(Paragraph("Art. 3 - Esclusioni", H2))
    S.append(Paragraph("La Societa' non riconosce alcuna prestazione quando il decesso sia causato da:", BODY))
    S.append(tabella([
        ["Causa esclusa", "Precisazione"],
        ["Competizioni motoristiche e motociclistiche",
         "comprese quelle amatoriali e di regolarita'"],
        ["Sport aerei di ogni genere", "compreso il volo da diporto sportivo"],
        ["Alpinismo, arrampicata, speleologia", "senza limite di quota o grado"],
        ["Immersione subacquea", "oltre i 30 metri di profondita'"],
        ["Uso di sostanze stupefacenti e stato di ebbrezza",
         "accertato con tasso superiore a 0,8 g/l"],
        ["Atto doloso dell'assicurato", "-"],
        ["Guerra, insurrezione, eventi nucleari", "senza periodo di tolleranza"],
        ["Epidemie e pandemie dichiarate",
         "quando l'autorita' sanitaria abbia dichiarato lo stato di emergenza"],
    ], [60 * mm, 105 * mm]))

    S.append(Spacer(1, 4))
    S.append(Paragraph("Art. 4 - Non contestabilita'", H2))
    S.append(Paragraph("Decorsi 12 mesi dalla data di effetto, la Societa' rinuncia a contestare le "
                       "dichiarazioni rese nel questionario sanitario, salvo il caso di dolo.", BODY))
    S.append(Paragraph("Art. 5 - Limitazione temporale di legge", H2))
    S.append(Paragraph("La garanzia non opera qualora il decesso avvenga entro <b>12 mesi</b> dalla data di "
                       "effetto per atto volontario dell'assicurato, secondo quanto previsto dalla normativa "
                       "applicabile.", BODY))

    S.append(PageBreak())
    S.append(Paragraph("GARANZIE COMPLEMENTARI", H1))
    S.append(tabella([
        ["Garanzia", "Operativita'", "Prestazione", "Premio"],
        ["Invalidita' permanente\nda infortunio",
         "compresa",
         "liquidazione del capitale\nresiduo se l'invalidita'\nriconosciuta e' pari o\nsuperiore al 66%; esclusa\nl'invalidita' da malattia",
         "inclusa"],
        ["Doppio capitale per\ninfortunio", "non prevista", "-", "-"],
        ["Inabilita' temporanea\ntotale",
         "opzionale, attivata\nin preventivo",
         "indennita' giornaliera di\n30 EUR, franchigia di\n60 giorni, massimo 180 gg",
         "48 EUR/anno\ncompresi nei\n156 EUR"],
        ["Malattie gravi", "compresa",
         "anticipazione del 30% del\ncapitale residuo alla diagnosi\ndi una delle 4 patologie\nelencate in allegato",
         "inclusa"],
    ], [38 * mm, 33 * mm, 62 * mm, 25 * mm]))

    S.append(Spacer(1, 8))
    S.append(Paragraph("SERVIZI ACCESSORI", H2))
    S.append(Paragraph("Il contratto comprende: secondo parere medico internazionale in caso di diagnosi "
                       "grave; un check-up annuale presso strutture convenzionate; portale di gestione della "
                       "posizione con firma digitale. Non e' prevista assistenza di urgenza a domicilio.", BODY))

    S.append(Spacer(1, 10))
    S.append(Paragraph("CONDIZIONI ECONOMICHE E DIRITTI DEL CONTRAENTE", H2))
    S.append(tabella([
        ["Voce", "Valore"],
        ["Caricamenti sul premio", "31,5% del premio lordo"],
        ["Diritto di recesso", "30 giorni dalla conclusione del contratto"],
        ["Rimborso in caso di recesso", "integrale, senza trattenute"],
        ["Adeguamento del premio",
         "la Societa' si riserva la revisione della tariffa al 5. e al 10. anno,\ncon facolta' di recesso del contraente"],
        ["Riscatto", "non previsto"],
    ], [60 * mm, 105 * mm]))

    SimpleDocTemplate(path, pagesize=A4, topMargin=18 * mm, bottomMargin=20 * mm,
                      leftMargin=20 * mm, rightMargin=20 * mm).build(
        S, onFirstPage=lambda c, d: footer(c, d, "NORDEST VITA"),
        onLaterPages=lambda c, d: footer(c, d, "NORDEST VITA"))


def genera(cartella):
    os.makedirs(cartella, exist_ok=True)
    a = os.path.join(cartella, "POLIZZA_A_VERSILIA_VITA.pdf")
    b = os.path.join(cartella, "POLIZZA_B_NORDEST_VITA.pdf")
    doc_a(a)
    doc_b(b)
    return a, b


if __name__ == "__main__":
    print(genera(sys.argv[1] if len(sys.argv) > 1 else "."))
