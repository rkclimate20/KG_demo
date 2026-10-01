#!/usr/bin/env python3
"""
build_semantic_kg_v2.py - build an interactive knowledge graph from an encyclopedia + the source papers.

Unlike a graph built only from Wikipedia links, this links terms using
  (1) co-occurrence in the papers (with the supporting sentence),
  (2) term structure ("MHW threshold" -> "MHW"),
  (3) abbreviations found in the text ("sea surface temperature (SST)"),
  (4) optionally, Wikipedia page links.

Example (Google Colab):
  !python /content/KG_demo/build_semantic_kg_v2.py \\
      --input  /content/encyclopediaTest.html \\
      --output /content/semantic_KG_v2.html \\
      --text-dir /content/text_all

Requires: beautifulsoup4 networkx pyvis pandas
"""
import re, os, sys, glob, math, argparse, collections, itertools, warnings
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
docs = []
for fp in files:
    with open(fp, encoding="utf-8", errors="ignore") as f:
        docs.append((os.path.basename(fp), f.read()))
if not docs:
    print(f"⚠️ No .txt papers found ({TEXT_DIR or '--text-dir not given'}). Co-occurrence and abbreviation links are skipped.")
    USE_COOCCURRENCE = USE_ABBREVIATIONS = False
else:
    print(f"Loaded {len(docs)} paper(s) from {TEXT_DIR}")

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
if USE_COOCCURRENCE:
    for _, text in docs:
        seen = set()
        for sents in paragraphs(text):
            prev = set()
            for s in sents:
                cur = find_terms(s)
                freq.update(cur); seen |= cur
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
               "connected": G.degree(n) > 0, "wikipedia": wiki_url.get(n, "")} for n in nodes]).to_csv(OUT_NODES, index=False)

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
                 title=(d["evidence"] or "")[:260], types=",".join(d["types"]))
net.barnes_hut(gravity=-9000, spring_length=130, central_gravity=0.2)
net.write_html(OUT_HTML)

legend_html = "".join(f'<div><span class="dot" style="background:{c}"></span>{l}</div>' for c, l in legend)
ui = """
<style>
.box{position:fixed;z-index:9999;background:rgba(0,0,0,.75);padding:10px;border-radius:8px;
     border:1px solid #66b2ff;color:#fff;font-family:Arial;font-size:13px}
#panel{top:20px;left:20px} #legend{top:20px;right:20px;max-width:260px}
.dot{width:12px;height:12px;border-radius:50%%;display:inline-block;margin-right:6px}
</style>
<div id="panel" class="box">🔍 <input id="search" placeholder="Search term"> <button onclick="searchNode()">Go</button>
 <button onclick="resetGraph()">Reset</button><br><br>
 <b>Show links</b><br>
 <label><input type="checkbox" class="et" value="cooc" checked> co-occur in papers</label><br>
 <label><input type="checkbox" class="et" value="hierarchy" checked> more specific term</label><br>
 <label><input type="checkbox" class="et" value="abbrev" checked> abbreviation</label><br>
 <label><input type="checkbox" class="et" value="wiki" checked> Wikipedia link</label><br><br>
 <span>%d of %d terms connected</span>
</div>
<div id="legend" class="box"><b>Topic clusters (size)</b><br>%s
 <div style="margin-top:6px;color:#aaa">Node size = mentions in papers<br>Hover a link for the supporting sentence<br>Click a node to open Wikipedia</div></div>
<script>
network.on("click", p => { if(p.nodes.length){ let n=network.body.data.nodes.get(p.nodes[0]); if(n.wiki) window.open(n.wiki,"_blank"); }});
function searchNode(){
  let v=document.getElementById("search").value.toLowerCase().trim(); if(!v) return;
  let all=network.body.data.nodes.get();
  let f=all.find(n=>n.label.toLowerCase()===v) || all.find(n=>n.label.toLowerCase().includes(v));
  if(!f) return alert("Term not in graph (it may have no links - see kg_nodes.csv)");
  let nb=network.getConnectedNodes(f.id);
  network.body.data.nodes.update(all.map(n=>({id:n.id,color:(n.id===f.id||nb.includes(n.id))?n.originalColor:"rgba(100,100,100,0.2)"})));
  network.focus(f.id,{scale:1.5});
}
function resetGraph(){
  let all=network.body.data.nodes.get();
  network.body.data.nodes.update(all.map(n=>({id:n.id,color:n.originalColor}))); network.fit();
}
document.getElementById("search").addEventListener("keydown",e=>{if(e.key==="Enter")searchNode();});
document.querySelectorAll(".et").forEach(cb=>cb.addEventListener("change",()=>{
  let on=[...document.querySelectorAll(".et:checked")].map(c=>c.value);
  network.body.data.edges.update(network.body.data.edges.get().map(e=>({id:e.id,hidden:!(e.types||"").split(",").some(t=>on.includes(t))})));
}));
</script>
""" % (len(connected), len(nodes), legend_html)
with open(OUT_HTML, encoding="utf-8") as f: page = f.read()
with open(OUT_HTML, "w", encoding="utf-8") as f: f.write(page.replace("</body>", ui + "</body>"))

print(f"\n🚀 Graph saved: {OUT_HTML}")
print(f"   Edge list : {OUT_EDGES}\n   Node table: {OUT_NODES}  (includes the {len(unconnected)} unconnected terms)")
if unconnected: print("   Unconnected examples:", ", ".join(unconnected[:15]))
