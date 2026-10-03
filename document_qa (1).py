"""Evidence-scoped informational QA. No product names or answers hard-coded."""
import json
import re
import unicodedata
from collections import Counter
import math

def tokens(text):
    text = unicodedata.normalize('NFKD', text.lower())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'\b(?:[a-z]\.){2,}[a-z]?\.?', lambda m: m[0].replace('.', ''), text)
    return re.findall(r'[a-z0-9]+', text)

def followup_reference(question):
    t = set(tokens(question))
    return bool(t & {'questo','questa','questi','queste','quello','quella','entrambi','entrambe'}) or bool(
        re.search(r'\b(?:sono compres[ioe]|devo acquistar|continuare a usare|dentro il mobile)\b', question, re.I))

def informational(question):
    return bool(re.search(r'differenz|finitur|material|sostegn|sostenut|fissat|montagg|compres|inclus|optional|accessori|telecomando|decoder|allungabil|come si apr|scorrevol|battente|lavab|pulir|pulizia|manutenz', question, re.I)) and not bool(
        re.search(r'budget|prezzo\s+(?:massimo|non|entro)|larghezza\s+(?:massima|minima)|altezza\s+(?:massima|minima)|pi[uù]\s+(?:largh|economic)', question, re.I))

class EvidenceIndex:
    def __init__(self, pages):
        self.pages = pages
        self.words = [Counter(tokens(p.get('compact') or p['text'])) for p in pages]
        self.titles = [set(tokens(p.get('title') or self.title(p['text']))) for p in pages]
        self.df = Counter(w for counts in self.words for w in counts)
        self.avg = sum(sum(c.values()) for c in self.words) / max(1,len(pages))

    @staticmethod
    def title(text):
        for line in text.splitlines():
            s=line.strip()
            if s and not s.startswith(('##','```','PAGINA_','---')):
                return re.split(r'\s{3,}|//',s)[0]
        return ''

    def retrieve(self, question, identities='', limit=16, max_chars=65000):
        query=set(tokens(question))
        # Generic bilingual catalog vocabulary, never brand-specific answers.
        equivalents={'armadi':{'wardrobe','wardrobes'},'armadio':{'wardrobe','wardrobes'},
            'letto':{'bed'},'letti':{'bed','beds'},'tavolo':{'table'},'tavoli':{'table','tables'},
            'allungabili':{'extendable','extendible'},'allungabile':{'extendable','extendible'},
            'bagno':{'bathroom'},'lavabi':{'washbasin','basin','lavabo'},
            'lavabo':{'washbasin','basin'},'scorrevoli':{'sliding'},'battente':{'hinged'},
            'pulire':{'cleaning','maintenance'},'pulizia':{'cleaning'}}
        for w in list(query): query.update(equivalents.get(w,set()))
        identity=set(tokens(identities))
        exact_ids={w for w in query|identity if re.search(r'[a-z]',w) and re.search(r'\d',w)}
        # Document-derived rare title words identify a named family (including N.O.W.).
        title_df=Counter(w for title in self.titles for w in title)
        anchors={w for w in query|identity if w in title_df and len(w)>2
                 and title_df[w] < max(20,len(self.pages)*.12)}
        identity_pages=[i for i,c in enumerate(self.words) if exact_ids & set(c)]
        for i in identity_pages:
            anchors.update(w for w in self.titles[i] if len(w)>2 and title_df[w]<max(20,len(self.pages)*.12))
        if identity_pages:
            anchors -= {'tv','units','unit','bed','beds','table','tables','wardrobe','wardrobes','composition','composizioni'}
        categories={'tv','bed','beds','table','tables','wardrobe','wardrobes','sideboard','sideboards','shelf','shelves'}
        identity_categories=set().union(*(self.titles[i]&categories for i in identity_pages)) if identity_pages else set()
        def matching_family(i):
            return bool(anchors & self.titles[i]) and (not identity_categories or bool(identity_categories & self.titles[i]))
        family_numbers={int(p['page']) for i,p in enumerate(self.pages) if matching_family(i)}
        scores=[]
        for i,(p,c) in enumerate(zip(self.pages,self.words)):
            score=0.0; length=sum(c.values())
            for w in query|identity:
                f=c[w]
                if f:
                    idf=math.log(1+(len(self.pages)-self.df[w]+.5)/(self.df[w]+.5))
                    score += idf*f*2.2/(f+1.2*(.25+.75*length/max(1,self.avg)))
            score += 18*len(anchors & self.titles[i]) if matching_family(i) else 0
            attached=bool(self.titles[i] & {'optional','optionals','accessori','accessories'}) and any(
                0<int(p['page'])-n<=6 for n in family_numbers)
            if attached: score+=45
            code_hits=({str(x).lower() for x in p.get('codes',set())}|set(c)) & exact_ids
            score += 100*len(code_hits)
            if score: scores.append((score,i))
        scores.sort(reverse=True)
        selected=[]
        # Exact identity pages and named-family technical pages precede generic optional pages.
        for _,i in scores:
            attached=bool(self.titles[i] & {'optional','optionals','accessori','accessories'}) and any(
                0<int(self.pages[i]['page'])-n<=6 for n in family_numbers)
            if attached or matching_family(i) or (({str(x).lower() for x in self.pages[i].get('codes',set())}|set(self.words[i]))&exact_ids):
                selected.append(i)
        selected=selected[:limit]
        for _,i in scores:
            if len(selected)>=limit: break
            if i not in selected: selected.append(i)
        blocks=[]; used={}; size=0
        for i in selected:
            p=self.pages[i]; body=p.get('compact') or p['text']
            block=f"\n===== PAGINA PDF {p['page']} =====\n{body}\n"
            if size+len(block)>max_chars: continue
            blocks.append(block); used[int(p['page'])]=body; size+=len(block)
        catalog='\n'.join(f"{p['page']}: {self.title(p['text'])}" for p in self.pages)
        return ''.join(blocks),used,catalog

def parse_json(text):
    text=re.sub(r'^```(?:json)?\s*|\s*```$', '',text.strip())
    return json.loads(text)

def verified_claims(payload, pages):
    """Every displayed claim needs literal quotations on retrieved pages."""
    accepted=[]; rejected=[]
    for claim in payload.get('claims',[]):
        evidence=claim.get('evidence',[])
        ok=bool(evidence) and bool(claim.get('text'))
        for e in evidence:
            try: page=int(e['page']); quote=' '.join(e['quote'].split())
            except (KeyError,ValueError,TypeError): ok=False; break
            if len(quote)<12 or quote not in ' '.join(pages.get(page,'').split()): ok=False
        (accepted if ok else rejected).append(claim)
    return accepted,rejected

EXTRACT_PROMPT='''Sei il Narratore documentale. Estrai solo fatti che rispondono alla domanda sul prodotto richiesto. La memoria identifica gli oggetti, non è una fonte. Non trasferire optional, reti, clausole o inclusioni fra famiglie. Un codice separato di un altro prodotto non dimostra esclusione dal prezzo del prodotto richiesto. Un fissaggio a parete non esclude un appoggio a terra. Una quota disegnata non dimostra una funzione. Distingui optional, incluso ed evidenza insufficiente. Non dichiarare assenza nell'intero catalogo dalla sola ricerca: l'indice dei titoli serve a individuare pagine mancanti. Nessun preventivo o prodotto selezionabile. Rispondi in JSON: {"claims":[{"text":"fatto breve italiano", "evidence":[{"page":53,"quote":"citazione letterale continua"}]}],"unresolved":["aspetto della domanda non provato"],"needed_pages":[532]}. Ogni fatto deve avere prove letterali dalla stessa famiglia pertinente. Non formulare consigli esterni.'''

AUDIT_PROMPT='''Sei il Superrisponditore revisore. Controlla domanda, fatti candidati e prove. Elimina ogni conclusione che le citazioni non dimostrano, anche se plausibile; controlla prodotto/famiglia, optional contro incluso, contraddizioni, memoria, e ogni parte della domanda. Non basta che una citazione esista: deve implicare il fatto. Non trasferire fatti tra prodotti. Restituisci lo stesso JSON claims/evidence/unresolved/needed_pages. Puoi conservare solo citazioni presenti nelle pagine fornite. Non aggiungere prezzi o prodotti non richiesti. Non inferire assenza globale da estratti.'''

def answer_question(question, identities, index, generate, disclaimer):
    dossier,pages,catalog=index.retrieve(question,identities)
    request=f'DOMANDA: {question}\nIDENTITA DAL DIALOGO: {identities}\nINDICE TITOLI:\n{catalog}\nPAGINE:\n{dossier}'
    candidate=parse_json(generate(EXTRACT_PROMPT,request,2600,.0))
    # A single bounded recovery fetches explicit source pages identified in the index.
    needed={int(n) for n in candidate.get('needed_pages',[]) if str(n).isdigit()}-set(pages)
    extra=[p for p in index.pages if int(p['page']) in needed][:8]
    for p in extra:
        body=p.get('compact') or p['text']; pages[int(p['page'])]=body
        request+=f"\n===== PAGINA PDF {p['page']} =====\n{body}\n"
    if extra: candidate=parse_json(generate(EXTRACT_PROMPT,request,2600,.0))
    accepted,rejected=verified_claims(candidate,pages)
    candidate['claims']=accepted
    audited=parse_json(generate(AUDIT_PROMPT,request+'\nFATTI CANDIDATI:\n'+json.dumps(candidate,ensure_ascii=False),2600,.0))
    final,rejected2=verified_claims(audited,pages)
    lines=[]
    for c in final:
        refs=', '.join(str(n) for n in sorted({int(e['page']) for e in c['evidence']}))
        lines.append(c['text'].strip()+f' (pag. {refs}).')
    unresolved=audited.get('unresolved',[])
    if unresolved: lines.append('Da chiarire sul documento: '+'; '.join(str(x) for x in unresolved))
    if rejected or rejected2: lines.append('Alcune affermazioni non hanno superato il controllo delle prove e sono state omesse.')
    if not final: lines.insert(0,'Non ho trovato prove sufficienti nelle pagine recuperate per rispondere con certezza a questa domanda. Questo non dimostra che l’informazione sia assente dal catalogo.')
    return '\n\n'.join(lines)+'\n\n'+disclaimer
