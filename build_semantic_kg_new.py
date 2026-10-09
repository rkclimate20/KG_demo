#!/usr/bin/env python3
"""
build_semantic_kg.py - build an interactive knowledge graph from an encyclopedia + the source papers.

Unlike a graph built only from Wikipedia links, this links terms using
  (1) co-occurrence in the papers (with the supporting sentence),
  (2) term structure ("MHW threshold" -> "MHW"),
  (3) abbreviations found in the text ("sea surface temperature (SST)"),
  (4) optionally, Wikipedia page links.

Example (Google Colab):
  !python /content/KG_demo/build_semantic_kg.py \\
      --input  /content/encyclopediaTest.html \\
      --output /content/semantic_KG_v2.html \\
      --text-dir /content/text_all \\
      --papers-dir /content/marine

In the generated graph: click a node to highlight only that term and its connections, and see the
papers (with links to Europe PMC) and sentences where it is mentioned.

Requires: beautifulsoup4 networkx pyvis pandas
"""
import re, os, sys, glob, json, math, argparse, collections, itertools, warnings
from bs4 import BeautifulSoup
import networkx as nx
from pyvis.network import Network
import pandas as pd

warnings.filterwarnings("ignore")

ap = argparse.ArgumentParser(description="Build an interactive knowledge graph from an encyclopedia HTML and the source papers.")
ap.add_argument("--input", "-i", required=True, help="encyclopedia HTML (list of terms), e.g. encyclopediaTest.html")
ap.add_argument("--output", "-o", required=True, help="output interactive graph (HTML)")
ap.add_argument("--text-dir", "-t", default=None,
                help="folder with plain-text papers (*.txt, e.g. txt2phrases xml2txt output). "
                     "Enables co-occurrence + abbreviation links. Without it only term-structure links are built.")
ap.add_argument("--papers-dir", "-p", default=None,
                help="folder with the downloaded papers (pygetpapers output, e.g. /content/marine). Used to show each paper's "
                     "title, authors and year. Without it papers are listed by ID (PMC...) with a link to Europe PMC.")
ap.add_argument("--merged-csv", default=None, help="optional merged keyphrase CSV; used with --add-merged-phrases")
ap.add_argument("--add-merged-phrases", action="store_true", help="also add phrases from --merged-csv that are not in the encyclopedia")
ap.add_argument("--min-phrase-count", type=int, default=3, help="min count for --add-merged-phrases (default 3)")
ap.add_argument("--edges-csv", default=None, help="edge list CSV (default: <output>_edges.csv)")
ap.add_argument("--nodes-csv", default=None, help="node table CSV (default: <output>_nodes.csv)")
ap.add_argument("--min-cooc", type=float, default=1.0, help="min co-occurrence weight to keep a link (default 1; try 2-3 for 50+ papers)")
ap.add_argument("--top-k", type=int, default=6, help="keep each term's K strongest co-occurrence links (default 6)")
ap.add_argument("--max-fanout", type=int, default=40, help="skip hierarchy links to terms contained in more than N terms (default 40)")
ap.add_argument("--extra-noise", default="", help="comma-separated extra terms to drop, e.g. 'figure,table'")
ap.add_argument("--no-cooccurrence", action="store_true", help="disable co-occurrence links")
ap.add_argument("--no-hierarchy", action="store_true", help="disable term-structure links")
ap.add_argument("--no-abbreviations", action="store_true", help="disable abbreviation detection")
ap.add_argument("--wiki-links", action="store_true", help="also add old-style Wikipedia page links (can be noisy)")
args = ap.parse_args()

ENCYCLOPEDIA_HTML = args.input
OUT_HTML   = args.output
_stem      = os.path.splitext(OUT_HTML)[0]
OUT_EDGES  = args.edges_csv or _stem + "_edges.csv"
OUT_NODES  = args.nodes_csv or _stem + "_nodes.csv"
TEXT_DIR   = args.text_dir
PAPERS_DIR = args.papers_dir
OUT_PAPERS = _stem + "_papers.csv"
MERGED_CSV = args.merged_csv

USE_COOCCURRENCE  = not args.no_cooccurrence
USE_HIERARCHY     = not args.no_hierarchy
USE_ABBREVIATIONS = not args.no_abbreviations
USE_WIKI_LINKS    = args.wiki_links
MIN_COOC, TOP_K, MAX_FANOUT = args.min_cooc, args.top_k, args.max_fanout
ADD_MERGED_PHRASES, MIN_PHRASE_COUNT = args.add_merged_phrases, args.min_phrase_count

if not os.path.isfile(ENCYCLOPEDIA_HTML):
    sys.exit(f"Input file not found: {ENCYCLOPEDIA_HTML}")

# Paper boilerplate and figure-description words that are not scientific concepts (edit freely)
NOISE_TERMS = {"consistent", "investigation", "characteristic", "amplify", "averaged", "oceanic term",
               "ile temperature", "identified", "broader", "coherent", "slower", "blue", "software", "resources",
               "methodology", "figs", "arrows", "stipples", "terlies", "ppvf", "anomal", "predict",
               "communications", "competing interests", "conceptualization", "data availability",
               "formal analysis", "funding acquisition", "project administration", "international license",
               "nature publishing group", "supplementary dataset", "supplementary movie",
               "supplementary information", "github repository", "mathworks", "training data",
               "publicly accessible repositories", "data processing"}
NOISE_TERMS |= {x.strip().lower() for x in args.extra_noise.split(",") if x.strip()}
NOISE_PATTERN = re.compile(r"\b(gray|grey|black|red|blue|orange|magenta|dashed|solid|shaded?|shading|dots?|boxe?s?|"
                           r"contours?|stipples?)\b", re.I)   # figure-caption words: "gray shading", "black boxes", ...

# ---------------- Text normalisation ----------------
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

def stem(w):
    w = w.lower()
    if len(w) > 4 and w.endswith("ies"):                      return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("sses", "xes", "ches", "shes")): return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")): return w[:-1]
    return w

def norm(text):
    return tuple(stem(w) for w in TOKEN_RE.findall(text))

# ---------------- 1. Load terms ----------------
with open(ENCYCLOPEDIA_HTML, encoding="utf-8") as f:
    html = re.sub(r"<sup[^>]*>.*?</sup>", "", f.read(), flags=re.S)
soup = BeautifulSoup(html, "html.parser")

DISAMBIG = ("may refer to", "may be an abbreviation", "may stand for", "may also refer")
raw_terms, wiki_url, entry_links, is_disambig = [], {}, {}, {}
for div in soup.find_all("div", {"role": "ami_entry"}):
    t = (div.get("term") or "").strip()
    if not t or t.lower() in NOISE_TERMS or len(t) < 3 or t.isdigit() or NOISE_PATTERN.search(t):
        continue
    raw_terms.append(t)
    a = div.find("a", class_="wikipedia-link")
    wiki_url[t] = a["href"] if a else ""
    p = div.find("p", class_="wpage_first_para")
    is_disambig[t] = bool(p and any(k in p.get_text(" ") for k in DISAMBIG))
    links = set()
    for a in div.find_all("a", href=True):
        if "wikipedia-link" in (a.get("class") or []) or "wikidata-link" in (a.get("class") or []):
            continue
        m = re.search(r"/wiki/([^\"#]+)", a["href"])
        if m:
            links.add(m.group(1).replace("_", " "))
    entry_links[t] = links

if ADD_MERGED_PHRASES and MERGED_CSV and os.path.exists(MERGED_CSV):
    try:
        df = pd.read_csv(MERGED_CSV)
        low = {c.lower(): c for c in df.columns}
        pc = next((low[c] for c in ("phrase", "keyphrase", "term", "word", "text", "name") if c in low), df.columns[0])
        cc = next((low[c] for c in ("count", "freq", "frequency", "counts", "total") if c in low), None)
        if cc:
            df = df[pd.to_numeric(df[cc], errors="coerce").fillna(0) >= MIN_PHRASE_COUNT]
        extra = [str(x).strip() for x in df[pc].dropna().unique()]
        known = {norm(t) for t in raw_terms}
        added = [t for t in extra if len(t) >= 3 and not t.isdigit() and norm(t) and norm(t) not in known
                 and t.lower() not in NOISE_TERMS and not NOISE_PATTERN.search(t)]
        raw_terms += added
        for t in added: wiki_url[t] = ""; entry_links[t] = set(); is_disambig[t] = False
        print(f"Added {len(added)} phrases from merged.csv")
    except Exception as e:
        print("Could not read merged.csv:", e)

# Merge trivial variants (plural / case): 'Anomalies' + 'anomaly', 'marine heatwaves' + 'marine heatwave'
vocab, display = {}, {}            # normalised tuple -> node id ; node id -> label
groups = collections.defaultdict(list)
for t in raw_terms:
    k = norm(t)
    if k: groups[k].append(t)
for k, variants in groups.items():
    label = next((v for v in variants if v == v.lower()), variants[0])
    vocab[k] = label
    display[label] = label
    for v in variants:
        wiki_url.setdefault(label, wiki_url.get(v, ""))
        if not wiki_url[label]: wiki_url[label] = wiki_url.get(v, "")
        entry_links.setdefault(label, set()).update(entry_links.get(v, set()))
        is_disambig[label] = is_disambig.get(label, False) and is_disambig.get(v, False)
nodes = sorted(display)
print(f"Terms in encyclopedia: {len(raw_terms)}  ->  {len(nodes)} nodes after merging plural/case variants")

# ---------------- 2. Load the papers ----------------
files = sorted(glob.glob(os.path.join(TEXT_DIR, "**", "*.txt"), recursive=True)) if TEXT_DIR else []
def paper_id(fp):
    """PMC id from the path (xml2txt keeps it in the file or folder name); otherwise the file name."""
    m = re.search(r"PMC\d+", os.path.relpath(fp, TEXT_DIR), re.I)
    return m.group(0).upper() if m else os.path.splitext(os.path.basename(fp))[0]

docs = []
for fp in files:
    with open(fp, encoding="utf-8", errors="ignore") as f:
        docs.append((paper_id(fp), f.read()))
if not docs:
    print(f"⚠️ No .txt papers found ({TEXT_DIR or '--text-dir not given'}). Co-occurrence and abbreviation links are skipped.")
    USE_COOCCURRENCE = USE_ABBREVIATIONS = False
else:
    print(f"Loaded {len(docs)} paper(s) from {TEXT_DIR}")

def load_meta(pid):
    """Title / authors / year / link for a paper, from pygetpapers metadata if available."""
    meta = {"title": pid, "authors": "", "year": "", "url": ""}
    doi = ""
    if PAPERS_DIR:
        hits = glob.glob(os.path.join(PAPERS_DIR, "**", pid), recursive=True)
        folder = next((h for h in hits if os.path.isdir(h)), None)
        if folder:
            jf, xf = os.path.join(folder, "eupmc_result.json"), os.path.join(folder, "fulltext.xml")
            try:
                if os.path.exists(jf):
                    j = json.load(open(jf, encoding="utf-8"))
                    meta["title"] = re.sub(r"<[^>]+>", "", j.get("title") or pid).strip()
                    meta["authors"] = j.get("authorString", "") or ""
                    meta["year"] = str(j.get("pubYear", "") or "")
                    doi = j.get("doi", "") or ""
                elif os.path.exists(xf):
                    m = re.search(r"<article-title[^>]*>(.*?)</article-title>", open(xf, encoding="utf-8", errors="ignore").read(), re.S)
                    if m: meta["title"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()
            except Exception:
                pass
    if pid.upper().startswith("PMC"): meta["url"] = f"https://europepmc.org/article/PMC/{pid.upper()}"
    elif doi: meta["url"] = f"https://doi.org/{doi}"
    return meta

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
def paragraphs(text):
    for para in re.split(r"\n\s*\n", text):
        para = re.sub(r"\s+", " ", para).strip()
        if para:
            yield [s for s in SENT_SPLIT.split(para) if s.strip()]

# ---------------- 3. Abbreviations: "sea surface temperature (SST)" ----------------
def best_long_form(short, long_text):                 # Schwartz & Hearst (2003)
    s, l = len(short) - 1, len(long_text) - 1
    while s >= 0:
        c = short[s].lower()
        if not c.isalnum():
            s -= 1; continue
        while l >= 0 and (long_text[l].lower() != c or (s == 0 and l > 0 and long_text[l - 1].isalnum())):
            l -= 1
        if l < 0: return None
        s -= 1; l -= 1
    return long_text[long_text.rfind(" ", 0, l + 1) + 1:]

ABBR_RE = re.compile(r"\(([A-Za-z][A-Za-z0-9\-]{1,9})\)")
abbrev_pairs = collections.Counter()
aliases = {}
if USE_ABBREVIATIONS:
    for _, text in docs:
        flat = re.sub(r"\s+", " ", text)
        for m in ABBR_RE.finditer(flat):
            short = m.group(1)
            if not any(ch.isupper() for ch in short): continue
            before = flat[:m.start()].rstrip().split(" ")
            n = min(len(short) + 5, len(short) * 2)
            cand = " ".join(before[-n:])
            for sf in {short, short.rstrip("s")}:
                lf = best_long_form(sf, cand) if len(sf) >= 2 else None
                if lf and len(lf.split()) >= 2:
                    abbrev_pairs[(sf, lf.strip(" ,;:"))] += 1
                    break
    for (sf, lf), c in abbrev_pairs.most_common():
        ks, kl = norm(sf), norm(lf)
        if not ks or not kl or ks == kl: continue
        a, b = vocab.get(ks), vocab.get(kl)
        if a and not b:   vocab[kl] = a; aliases.setdefault(a, set()).add(lf)    # long form -> existing acronym node
        elif b and not a: vocab[ks] = b; aliases.setdefault(b, set()).add(sf)    # acronym -> existing long-form node
    print(f"Abbreviation pairs detected in text: {len(abbrev_pairs)} (aliases added: {sum(len(v) for v in aliases.values())})")

# ---------------- 4. Find terms in every sentence ----------------
MAXN = max(len(k) for k in vocab)
def find_terms(sentence):
    toks = [stem(w) for w in TOKEN_RE.findall(sentence)]
    found = set()
    for i in range(len(toks)):
        for n in range(1, min(MAXN, len(toks) - i) + 1):
            node = vocab.get(tuple(toks[i:i + n]))
            if node: found.add(node)
    return found

freq, docfreq = collections.Counter(), collections.Counter()
w_same, w_adj, evidence = collections.Counter(), collections.Counter(), {}
term_paper = collections.defaultdict(collections.Counter)      # term -> {paper id: mentions}
snippet = {}                                                   # (term, paper id) -> first sentence
if docs:
    for pid, text in docs:
        seen = set()
        for sents in paragraphs(text):
            prev = set()
            for s in sents:
                cur = find_terms(s)
                freq.update(cur); seen |= cur
                for t in cur:
                    term_paper[t][pid] += 1
                    snippet.setdefault((t, pid), s)
                for a, b in itertools.combinations(sorted(cur), 2):
                    w_same[(a, b)] += 1
                    evidence.setdefault((a, b), s)
                for a in cur:
                    for b in prev:
                        if a != b and b not in cur and a not in prev:   # pair not already counted in one sentence
                            w_adj[tuple(sorted((a, b)))] += 1
                prev = cur
        docfreq.update(seen)

# ---------------- 5. Collect edges by layer ----------------
edges = collections.defaultdict(lambda: {"types": set(), "weight": 0.0, "evidence": ""})
def add_edge(a, b, typ, w=1.0, ev=""):
    if a == b: return
    k = tuple(sorted((a, b)))
    e = edges[k]; e["types"].add(typ); e["weight"] += w
    if ev and not e["evidence"]: e["evidence"] = ev

# 5a. co-occurrence, scored with the Dice coefficient so very common words don't connect to everything
if USE_COOCCURRENCE:
    pairs = {}
    for k in set(w_same) | set(w_adj):
        w = w_same[k] + 0.5 * w_adj[k]
        if w >= MIN_COOC:
            pairs[k] = (2 * w / (freq[k[0]] + freq[k[1]]), w)
    by_node = collections.defaultdict(list)
    for k, (dice, w) in pairs.items():
        by_node[k[0]].append((dice, k)); by_node[k[1]].append((dice, k))
    keep = set()
    for n, lst in by_node.items():
        keep.update(k for _, k in sorted(lst, reverse=True)[:TOP_K])
    for k in keep:
        add_edge(*k, "cooc", pairs[k][1], evidence.get(k, ""))

# 5b. hierarchy: a term that contains another term is a more specific version of it
if USE_HIERARCHY:
    node_key = {}
    for k, n in vocab.items(): node_key.setdefault(n, k)
    cand, fan = [], collections.Counter()
    for n, k in node_key.items():
        for L in range(1, len(k)):
            for i in range(len(k) - L + 1):
                sub = vocab.get(k[i:i + L])
                if sub and sub != n:
                    cand.append((n, sub)); fan[sub] += 1
    skipped = sorted({s for s, c in fan.items() if c > MAX_FANOUT})
    for n, sub in cand:
        if fan[sub] <= MAX_FANOUT: add_edge(n, sub, "hierarchy", 1.0, f"'{n}' is a more specific form of '{sub}'")
    if skipped: print("Hierarchy skipped for very generic terms:", ", ".join(skipped))

# 5c. abbreviations / expansions that are both terms
if USE_ABBREVIATIONS:
    for (sf, lf), c in abbrev_pairs.items():
        a, b = vocab.get(norm(sf)), vocab.get(norm(lf))
        if a and b and a != b:
            add_edge(a, b, "abbrev", 1.0, f"'{sf}' is the abbreviation of '{lf}'")

# 5d. optional: Wikipedia page links (old method)
if USE_WIKI_LINKS:
    for n in nodes:
        if is_disambig.get(n): continue                       # a disambiguation page has meaningless links
        for tgt in entry_links.get(n, ()):
            m = vocab.get(norm(tgt))
            if m and m != n: add_edge(n, m, "wiki", 1.0, "Linked in Wikipedia")

# ---------------- 6. Graph, clusters, coverage ----------------
G = nx.Graph()
G.add_nodes_from(nodes)
for (a, b), e in edges.items():
    G.add_edge(a, b, weight=e["weight"], types=sorted(e["types"]), evidence=e["evidence"])
connected = [n for n in G if G.degree(n) > 0]
unconnected = sorted(set(nodes) - set(connected))
print(f"\n✅ Connected terms: {len(connected)} / {len(nodes)}   |   edges: {G.number_of_edges()}")
for t in ("cooc", "hierarchy", "abbrev", "wiki"):
    c = sum(1 for _, _, d in G.edges(data=True) if t in d["types"])
    if c: print(f"   {t:<10} edges: {c}")

H = G.subgraph(connected)
try:
    comms = nx.community.louvain_communities(H, weight="weight", seed=42)
except Exception:
    comms = list(nx.community.greedy_modularity_communities(H, weight="weight"))
comms = sorted(comms, key=len, reverse=True)
PALETTE = ["#66b2ff", "#ff9933", "#00ff99", "#ff5555", "#c792ea", "#ffd54f", "#4dd0e1",
           "#f06292", "#aed581", "#ba68c8", "#90a4ae", "#ffab91"]
node_color, legend = {}, []
for i, c in enumerate(comms):
    hub = max(c, key=lambda n: H.degree(n, weight="weight"))
    col = PALETTE[i] if i < len(PALETTE) else "#888888"
    for n in c: node_color[n] = col
    if i < len(PALETTE): legend.append((col, f"{hub} ({len(c)})"))
for n in unconnected: node_color[n] = "#555555"

# ---------------- 7. Save tables ----------------
pd.DataFrame([{"source": a, "target": b, "types": "+".join(d["types"]), "weight": d["weight"], "evidence": d["evidence"]}
              for a, b, d in G.edges(data=True)]).to_csv(OUT_EDGES, index=False)
pd.DataFrame([{"term": n, "mentions": freq[n], "papers": docfreq[n], "degree": G.degree(n),
               "connected": G.degree(n) > 0, "wikipedia": wiki_url.get(n, ""),
               "paper_ids": ";".join(p for p, _ in term_paper[n].most_common())} for n in nodes]).to_csv(OUT_NODES, index=False)

paper_meta = {pid: load_meta(pid) for pid, _ in docs}
paper_terms = collections.defaultdict(list)
for t in connected:
    for pid, c in term_paper[t].items(): paper_terms[pid].append([t, c])
for pid in paper_terms: paper_terms[pid].sort(key=lambda x: -x[1])
pd.DataFrame([{"paper_id": pid, **m, "connected_terms": len(paper_terms[pid])} for pid, m in paper_meta.items()]).to_csv(OUT_PAPERS, index=False)

# ---------------- 8. Interactive visualisation ----------------
EDGE_COLOR = {"cooc": "rgba(0,200,255,0.45)", "hierarchy": "rgba(255,153,51,0.75)",
              "abbrev": "rgba(255,0,200,0.9)", "wiki": "rgba(0,255,153,0.7)"}
net = Network(height="100vh", width="100%", bgcolor="#000000", font_color="#FFFFFF", cdn_resources="remote")
for n in connected:
    tip = f"{n}\nmentions: {freq[n]} | papers: {docfreq[n]} | links: {G.degree(n)}"
    if aliases.get(n): tip += "\nalso: " + ", ".join(sorted(aliases[n]))
    net.add_node(n, label=n, title=tip, color=node_color[n], originalColor=node_color[n],
                 size=8 + 5 * math.log2(1 + freq[n]) + 0.4 * G.degree(n), wiki=wiki_url.get(n, ""))
for a, b, d in G.edges(data=True):
    prim = next(t for t in ("abbrev", "hierarchy", "cooc", "wiki") if t in d["types"])
    net.add_edge(a, b, color=EDGE_COLOR[prim], width=1 + math.log2(1 + d["weight"]) * 0.6,
                 title=(d["evidence"] or "")[:260], types=",".join(d["types"]), w=round(d["weight"], 2))
net.barnes_hut(gravity=-9000, spring_length=130, central_gravity=0.2)
net.write_html(OUT_HTML)

legend_html = "".join(f'<div><span class="dot" style="background:{c}"></span>{l}</div>' for c, l in legend)

def js_json(obj):                       # safe to embed inside <script>
    return json.dumps(obj, ensure_ascii=True).replace("</", "<\\/")

papers_js = {pid: m for pid, m in paper_meta.items()}
term_papers_js = {t: [[pid, c, (snippet.get((t, pid), ""))[:260]] for pid, c in term_paper[t].most_common()]
                  for t in connected if term_paper[t]}

ui = """
<style>
.box{position:fixed;z-index:9999;background:rgba(0,0,0,.82);padding:10px;border-radius:8px;
     border:1px solid #66b2ff;color:#fff;font-family:Arial,sans-serif;font-size:13px}
#panel{top:20px;left:20px} #legend{top:20px;right:20px;max-width:260px}
#info{left:20px;bottom:20px;width:370px;max-height:52vh;overflow:auto;display:none}
#papers{right:20px;bottom:20px;width:330px;max-height:32vh;overflow:auto}
.dot{width:12px;height:12px;border-radius:50%;display:inline-block;margin-right:6px}
#info a,#papers a{color:#8fd0ff} #info h3{margin:0 0 4px 0;font-size:16px}
.muted{color:#aaa;font-size:12px} .item{margin:7px 0;padding-bottom:6px;border-bottom:1px solid #333}
.tag{font-size:11px;padding:1px 5px;border-radius:8px;background:#233;color:#9cf;margin-left:4px}
.nb{cursor:pointer;color:#ffd54f;text-decoration:underline}
mark{background:#ffd54f;color:#000;padding:0 1px}
.x{float:right;cursor:pointer;color:#aaa;font-size:16px}
</style>
<div id="panel" class="box">🔍 <input id="search" placeholder="Search term"> <button onclick="searchNode()">Go</button>
 <button onclick="clearSelection()">Reset</button><br><br>
 <b>Show links</b><br>
 <label><input type="checkbox" class="et" value="cooc" checked> co-occur in papers</label><br>
 <label><input type="checkbox" class="et" value="hierarchy" checked> more specific term</label><br>
 <label><input type="checkbox" class="et" value="abbrev" checked> abbreviation</label><br>
 <label><input type="checkbox" class="et" value="wiki" checked> Wikipedia link</label><br><br>
 <label><input type="checkbox" id="hideOthers"> hide unrelated nodes when selecting</label><br><br>
 <span>__CONN__ of __TOTAL__ terms connected</span><br>
 <span class="muted">Click a node to highlight it and its links. Click empty space or press Esc to reset.</span>
</div>
<div id="legend" class="box"><b>Topic clusters (size)</b><br>__LEGEND__
 <div style="margin-top:6px" class="muted">Node size = mentions in papers<br>Hover a link for the supporting sentence</div></div>
<div id="info" class="box"></div>
<div id="papers" class="box"><b>Source papers (__NPAPERS__)</b> <span class="muted">click a title to open; "terms" highlights its terms</span><div id="paperlist"></div></div>
<script>
const PAPERS = __PAPERS__;
const TERM_PAPERS = __TERMPAPERS__;
const PAPER_TERMS = __PAPERTERMS__;
const TYPE_NAME = {cooc:"co-occur", hierarchy:"more specific", abbrev:"abbreviation", wiki:"Wikipedia"};
const nodesDS = network.body.data.nodes, edgesDS = network.body.data.edges;
let selected = null, selPaper = null;

function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function markTerm(text, term){
  let t = esc(text);
  try { return t.replace(new RegExp("("+esc(term).replace(/[.*+?^${}()|[\\]\\\\]/g,"\\\\$&")+")","ig"), "<mark>$1</mark>"); } catch(e){ return t; }
}
function enabledTypes(){ return [...document.querySelectorAll(".et:checked")].map(c=>c.value); }
function edgeOn(e,on){ return (e.types||"").split(",").some(t=>on.includes(t)); }
function paperLink(pid){
  const p = PAPERS[pid] || {title:pid,url:""};
  const t = esc(p.title || pid);
  return p.url ? '<a href="'+esc(p.url)+'" target="_blank" rel="noopener">'+t+'</a>' : t;
}

function applyView(){
  const on = enabledTypes(), hideOthers = document.getElementById("hideOthers").checked;
  const edges = edgesDS.get();
  let focus = null;
  if (selected){
    focus = new Set([selected]);
    edges.forEach(e=>{ if (edgeOn(e,on) && (e.from===selected || e.to===selected)){ focus.add(e.from); focus.add(e.to);} });
  } else if (selPaper){
    focus = new Set((PAPER_TERMS[selPaper]||[]).map(x=>x[0]));
  }
  nodesDS.update(nodesDS.get().map(n=>{
    const inF = !focus || focus.has(n.id), isSel = n.id===selected;
    return {id:n.id, hidden: !!(focus && !inF && hideOthers),
            color: inF ? {background:n.originalColor, border: isSel?"#ffffff":n.originalColor,
                          highlight:{background:n.originalColor,border:"#ffffff"}}
                       : {background:"rgba(90,90,90,0.15)", border:"rgba(90,90,90,0.15)"},
            borderWidth: isSel?4:1,
            font:{color: inF ? "#ffffff" : "rgba(255,255,255,0.10)"}};
  }));
  edgesDS.update(edges.map(e=>{
    let vis = edgeOn(e,on);
    if (selected) vis = vis && (e.from===selected || e.to===selected);
    else if (selPaper) vis = vis && focus.has(e.from) && focus.has(e.to);
    const w0 = e.w0 || e.width || 1;
    return {id:e.id, hidden:!vis, w0:w0, width: selected ? Math.max(w0,2.5) : w0};
  }));
  return focus;
}

function fitFocus(focus){
  // zoom to the selection but keep it in the middle, clear of the side panels
  const ids = Array.from(focus); if(!ids.length) return;
  const pos = network.getPositions(ids); let x0=1e9,x1=-1e9,y0=1e9,y1=-1e9;
  ids.forEach(i=>{ const q=pos[i]; x0=Math.min(x0,q.x); x1=Math.max(x1,q.x); y0=Math.min(y0,q.y); y1=Math.max(y1,q.y); });
  const c = document.querySelector("canvas"), W = c.clientWidth, H = c.clientHeight;
  const sc = Math.min(1.2, W/((x1-x0)*1.8+250), H/((y1-y0)*1.8+250));
  network.moveTo({position:{x:(x0+x1)/2, y:(y0+y1)/2}, scale:sc, animation:{duration:500}});
}

function selectNode(id){
  selected = id; selPaper = null;
  const focus = applyView();
  showNodeInfo(id);
  fitFocus(focus);
}

function selectPaper(pid){
  selected = null; selPaper = pid;
  const focus = applyView();
  const p = PAPERS[pid] || {title:pid}, terms = PAPER_TERMS[pid] || [];
  let h = '<span class="x" onclick="clearSelection()">×</span><h3>'+paperLink(pid)+'</h3>';
  h += '<div class="muted">'+esc([p.authors,p.year].filter(Boolean).join(" · "))+'</div>';
  h += '<p>'+terms.length+' graph terms mentioned (click one to inspect):</p>';
  h += terms.map(t=>'<span class="nb" onclick="selectNode(\\''+esc(t[0]).replace(/'/g,"\\\\'")+'\\')">'+esc(t[0])+'</span> <span class="muted">×'+t[1]+'</span>').join(" · ");
  const box = document.getElementById("info"); box.innerHTML = h; box.style.display = "block";
  if (focus.size) fitFocus(focus);
}

function showNodeInfo(id){
  const n = nodesDS.get(id), on = enabledTypes();
  const links = edgesDS.get().filter(e=>edgeOn(e,on) && (e.from===id||e.to===id))
                 .map(e=>({other:e.from===id?e.to:e.from, types:e.types, w:e.w||0, ev:e.title||""}))
                 .sort((a,b)=>b.w-a.w);
  const tp = TERM_PAPERS[id] || [];
  let h = '<span class="x" onclick="clearSelection()">×</span><h3>'+esc(id)+'</h3>';
  h += '<div class="muted">'+links.length+' connections · mentioned in '+tp.length+' paper'+(tp.length===1?'':'s')+'</div>';
  if (n.wiki) h += '<div><a href="'+esc(n.wiki)+'" target="_blank" rel="noopener">Wikipedia ↗</a></div>';
  h += '<p><b>Papers mentioning this term</b></p>';
  if (!tp.length) h += '<div class="muted">Not found in the loaded papers (the term came from the encyclopedia).</div>';
  tp.forEach(x=>{
    h += '<div class="item">'+paperLink(x[0])+' <span class="muted">×'+x[1]+(PAPERS[x[0]]&&PAPERS[x[0]].year?' · '+esc(PAPERS[x[0]].year):'')+'</span>'
       + (x[2]?'<div class="muted">“'+markTerm(x[2],id)+'”</div>':'')+'</div>';
  });
  h += '<p><b>Connections</b> <span class="muted">(click to jump)</span></p>';
  links.forEach(l=>{
    h += '<div class="item"><span class="nb" onclick="selectNode(\\''+esc(l.other).replace(/'/g,"\\\\'")+'\\')">'+esc(l.other)+'</span>'
       + l.types.split(",").map(t=>'<span class="tag">'+(TYPE_NAME[t]||t)+'</span>').join("")
       + (l.ev?'<div class="muted">'+esc(l.ev.length>170?l.ev.slice(0,170)+"…":l.ev)+'</div>':'')+'</div>';
  });
  const box = document.getElementById("info"); box.innerHTML = h; box.style.display = "block"; box.scrollTop = 0;
}

function clearSelection(){
  selected = null; selPaper = null; applyView();
  document.getElementById("info").style.display = "none";
  network.fit({animation:{duration:400}});
}

function searchNode(){
  const v = document.getElementById("search").value.toLowerCase().trim(); if(!v) return;
  const all = nodesDS.get();
  const f = all.find(n=>n.label.toLowerCase()===v) || all.find(n=>n.label.toLowerCase().includes(v));
  if(!f) return alert("Term not in graph (it may have no links - see the _nodes.csv file)");
  selectNode(f.id);
}

network.on("click", p=>{
  if (p.nodes.length) selectNode(p.nodes[0]);
  else if (!p.edges.length && (selected||selPaper)) clearSelection();
});
document.getElementById("search").addEventListener("keydown", e=>{ if(e.key==="Enter") searchNode(); });
document.addEventListener("keydown", e=>{ if(e.key==="Escape") clearSelection(); });
document.querySelectorAll(".et,#hideOthers").forEach(cb=>cb.addEventListener("change", ()=>{
  applyView(); if (selected) showNodeInfo(selected);
}));

(function buildPaperList(){
  const box = document.getElementById("paperlist");
  const ids = Object.keys(PAPERS).sort((a,b)=>(PAPER_TERMS[b]||[]).length-(PAPER_TERMS[a]||[]).length);
  if (!ids.length){ box.innerHTML = '<div class="muted">No papers loaded (use --text-dir).</div>'; return; }
  box.innerHTML = ids.map(pid=>{
    const p = PAPERS[pid];
    return '<div class="item">'+paperLink(pid)+'<div class="muted">'+esc([p.authors,p.year].filter(Boolean).join(" · "))+'</div>'
         + '<span class="nb" onclick="selectPaper(\\''+esc(pid).replace(/'/g,"\\\\'")+'\\')">terms ('+(PAPER_TERMS[pid]||[]).length+')</span></div>';
  }).join("");
})();
</script>
"""
ui = (ui.replace("__CONN__", str(len(connected))).replace("__TOTAL__", str(len(nodes)))
        .replace("__LEGEND__", legend_html).replace("__NPAPERS__", str(len(papers_js)))
        .replace("__PAPERS__", js_json(papers_js)).replace("__TERMPAPERS__", js_json(term_papers_js))
        .replace("__PAPERTERMS__", js_json(dict(paper_terms))))
with open(OUT_HTML, encoding="utf-8") as f: page = f.read()
with open(OUT_HTML, "w", encoding="utf-8") as f: f.write(page.replace("</body>", ui + "</body>"))

print(f"\n🚀 Graph saved: {OUT_HTML}")
print(f"   Edge list : {OUT_EDGES}\n   Node table: {OUT_NODES}  (includes the {len(unconnected)} unconnected terms)\n   Papers    : {OUT_PAPERS}")
if unconnected: print("   Unconnected examples:", ", ".join(unconnected[:15]))
