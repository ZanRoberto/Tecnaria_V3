import os, glob, requests

def docs_folder():
    return os.path.abspath(os.environ.get("DOCS_FOLDER", "documenti_gTab"))

def github_autosync():
    """
    Se DOCS_FOLDER è vuota, scarica tutti i .txt da:
    https://api.github.com/repos/{OWNER}/{REPO}/contents/{DIR}?ref={BRANCH}
    """
    folder = docs_folder()
    os.makedirs(folder, exist_ok=True)
    if glob.glob(os.path.join(folder, "*.txt")):
        return  # già presenti, non fare nulla

    owner  = os.environ.get("GITHUB_OWNER",  "ZanRoberto")
    repo   = os.environ.get("GITHUB_REPO",   "Tecnaria_V3")
    subdir = os.environ.get("GITHUB_DIR",    "documenti_gTab")
    branch = os.environ.get("GITHUB_BRANCH", "main")
    token  = os.environ.get("GITHUB_TOKEN")  # opzionale se repo privata

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{subdir}?ref={branch}"
    headers = {"Accept":"application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    items = r.json()
    n = 0
    for it in items:
        if it.get("type") == "file" and it.get("name","").lower().endswith(".txt"):
            dl = it.get("download_url")
            if not dl:
                continue
            txt = requests.get(dl, timeout=20).text
            out = os.path.join(folder, it["name"])
            with open(out, "w", encoding="utf-8") as f:
                f.write(txt)
            n += 1
    print(f"[autosync] Scaricati {n} file .txt da GitHub in {folder}")
