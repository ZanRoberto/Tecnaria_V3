"""Extract table cells from any text PDF. No product or brand rules.
Usage: python build_catalog_cards.py catalog.pdf schede_prodotto.json
Requires PyMuPDF. Merged cells are inherited only where PDF cell geometry proves it.
"""
import fitz, json, re, sys, hashlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
DIM=re.compile(r'larghezza|width|profondit|depth|altezza|height|lunghezza|length|diametr|diameter',re.I)
CODE=re.compile(r'\bcodice\b|\bcode\b|\bsku\b|\bitem code\b',re.I)

def build(path, page_numbers=None):
 doc=fitz.open(path);cards=[];report={'pages':len(doc),'tables':0,'cards':0,'unparsed_tables':0}
 for idx in (page_numbers if page_numbers is not None else range(len(doc))):
  pno=idx+1;page=doc[idx]
  title=next((s.strip() for s in page.get_text(sort=True).splitlines() if len(s.strip())>2 and re.search(r'[A-Za-z]{2}',s)), '')
  for table in page.find_tables().tables:
   report['tables']+=1
   rows=table.extract();header=None;ci=None
   for ri,row in enumerate(rows):
    if any(CODE.search(v or '') for v in row):
     header=[re.sub(r'\s+',' ',v or '').strip() for v in row]
     ci=next(i for i,v in enumerate(header) if CODE.search(v));continue
    if header is None or ci>=len(row):continue
    code=(row[ci] or '').strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{3,}',code) or not any(c.isdigit() for c in code):continue
    attrs={};prices={};sources={}
    for col,label in enumerate(header):
     if col==ci or not label:continue
     raw=row[col];source_row=ri
     if raw is None:
      # A None is a vertically merged cell, not a blank: find its actual anchor.
      for prev in range(ri-1,-1,-1):
       if rows[prev][col] is not None:
        raw=rows[prev][col];source_row=prev;break
     value=re.sub(r'\s+',' ',raw or '').strip()
     if not value:continue
     if DIM.search(label):attrs[label]=value
     elif col>ci and re.fullmatch(r'\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:[.,]\d+)?',value):prices[label]=value
     else:continue
     sources[label]={'row':source_row,'column':col,'merged':source_row!=ri}
    if not attrs:continue
    cards.append({'codice':code,'pagina':pno,'prodotto':title,'attributi':attrs,'prezzi':prices,
                  'provenienza':{'table_bbox':list(table.bbox),'row':ri,'cells':sources}})
  if pno%100==0:print('pages',pno,flush=True)
 report['cards']=len(cards)
 return {'schema_version':2,'pdf_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'schede':cards,'report':report}
def part(args):
 return build(*args)
if __name__=='__main__':
 path=sys.argv[1];count=len(fitz.open(path))
 with ProcessPoolExecutor(max_workers=6) as pool:
  parts=list(pool.map(part,[(path,list(range(start,min(start+50,count)))) for start in range(0,count,50)]))
 result=parts[0];result['schede']=[c for part_result in parts for c in part_result['schede']]
 result['report']={'pages':count,'tables':sum(p['report']['tables'] for p in parts),'cards':len(result['schede'])}
 Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(result['report'])
