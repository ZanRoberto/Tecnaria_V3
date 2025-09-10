from scraper_tecnaria import kb
from flask import jsonify

@app.get("/ls")
def ls():
    return jsonify({
        "doc_dir": str(kb.doc_dir),
        "files_loaded": kb.files_loaded,
        "entries": len(kb.entries)
    })

@app.get("/debug")
def debug():
    q = request.args.get("q","che prodotti ha tecnaria")
    top = kb.debug_candidates(q, top=5)
    return jsonify({"q": q, "top": [{"file": f, "text": t, "score": s} for f, t, s in top]})
