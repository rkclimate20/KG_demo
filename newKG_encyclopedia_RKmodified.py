#!/usr/bin/env python3
import os, re, subprocess, webbrowser, warnings
from bs4 import BeautifulSoup
import networkx as nx
from pyvis.network import Network

# ==========================
# Setup
# ==========================
input_file = "/content/encyclopediaTest.html"
graph_html = "/content/4Climate_encyclopediaKG.html"

warnings.filterwarnings("ignore")

# ==========================
# Read HTML
# ==========================
with open(input_file, "r", encoding="utf-8") as f:
    html = f.read()

html = re.sub(r'<sup[^>]*>.*?</sup>', '', html, flags=re.S)
soup = BeautifulSoup(html, "html.parser")

entries = soup.find_all("div", {"role": "ami_entry"})

# ==========================
# Domain classifier
# ==========================
CLIMATE = {"climate","carbon","water","ecosystem","marine","temperature","groundwater"}
BIOLOGY = {"plant","organism","photosynthesis","algae","bacteria","oysters"}

def get_domain(term):
    t = term.lower()
    if any(k in t for k in CLIMATE): return "climate"
    if any(k in t for k in BIOLOGY): return "biology"
    return "other"

# ==========================
# Build graph
# ==========================
nodes = {}
href_pattern = re.compile(r"/wiki/([^\"#]+)")

for div in entries:
    term = (div.get("term") or "").strip()
    if not term:
        continue

    wiki_tag = div.find("a", class_="wikipedia-link")
    wiki_url = wiki_tag["href"] if wiki_tag else ""

    links = set()
    for a in div.find_all("a", href=True):
        m = href_pattern.search(a["href"])
        if m:
            links.add(m.group(1).replace("_", " ").strip())

    nodes[term] = {
        "wiki": wiki_url,
        "links": list(links),
        "domain": get_domain(term)
    }

G = nx.Graph()

for term, data in nodes.items():
    G.add_node(term, wiki=data["wiki"], domain=data["domain"])

lookup = {k.lower(): k for k in nodes}

for src, data in nodes.items():
    for tgt in data["links"]:
        tgt_key = lookup.get(tgt.lower())
        if tgt_key and tgt_key != src:
            G.add_edge(src, tgt_key)

G.remove_nodes_from(list(nx.isolates(G)))

# ==========================
# Visualization
# ==========================
net = Network(height="100vh", width="100%", bgcolor="#000000", font_color="#FFFFFF")

for node, data in G.nodes(data=True):
    deg = len(G[node])

    # ORIGINAL COLOR LOGIC
    color = "#00ff99" if deg > 25 else "#66b2ff" if deg > 10 else "#ff9933" if deg > 4 else "#ff5555"

    net.add_node(
        node,
        label=node,
        color=color,
        originalColor=color,   # ⭐ store original color
        size=10 + deg * 0.6,
        wiki=data.get("wiki", ""),
        group=data.get("domain")
    )

for src, tgt in G.edges():
    net.add_edge(src, tgt, color="rgba(0,255,255,0.85)", width=1.6)

html_path = os.path.abspath(graph_html)
net.write_html(html_path)

# ==========================
# Inject UI + Fix JS
# ==========================
with open(html_path, "r", encoding="utf-8") as f:
    html_data = f.read()

custom_ui = """
<style>
#panel {
 position: fixed; top:20px; left:20px; z-index:9999;
 background: rgba(0,0,0,0.7);
 padding:10px; border-radius:8px;
 border:1px solid #66b2ff;
 color:#fff; font-family:Arial;
}
#legend {
 position: fixed; top:20px; right:20px;
 background: rgba(0,0,0,0.7);
 padding:10px; border-radius:8px;
 border:1px solid #66b2ff;
 color:#fff;
}
.legend-dot {
 width:12px;height:12px;border-radius:50%;display:inline-block;margin-right:6px;
}
</style>

<div id="panel">
 🔍 <input id="search" placeholder="Search node"><br>
 <button onclick="searchNode()">Go</button><br><br>

 🎯 Filter:
 <select id="filter" onchange="filterDomain()">
  <option value="all">All</option>
  <option value="climate">Climate</option>
  <option value="biology">Biology</option>
  <option value="other">Other</option>
 </select><br><br>

 <button onclick="resetGraph()">Reset</button>
</div>

<div id="legend">
 <b>Node Colors</b><br>
 <div><span class="legend-dot" style="background:#00ff99"></span>Highly Connected</div>
 <div><span class="legend-dot" style="background:#66b2ff"></span>Main Topics</div>
 <div><span class="legend-dot" style="background:#ff9933"></span>Subfields</div>
 <div><span class="legend-dot" style="background:#ff5555"></span>Related Concepts</div>
</div>

<script>

// CLICK → Wikipedia (FIXED)
network.on("click", function(params){
 if(params.nodes.length > 0){
   let node = network.body.data.nodes.get(params.nodes[0]);
   if(node.wiki){
     window.open(node.wiki, "_blank");
   }
 }
});

// SEARCH + HIGHLIGHT
function searchNode(){
 let val = document.getElementById("search").value.toLowerCase();
 let nodes = network.body.data.nodes.get();

 let found = nodes.find(n => n.label.toLowerCase() === val);
 if(found){
   let connected = network.getConnectedNodes(found.id);

   network.body.data.nodes.update(nodes.map(n => ({
     id:n.id,
     color: (n.id===found.id || connected.includes(n.id)) ? n.originalColor : "rgba(100,100,100,0.2)"
   })));

   network.focus(found.id, {scale:1.6});
 }
}

// FILTER
function filterDomain(){
 let val = document.getElementById("filter").value;
 let nodes = network.body.data.nodes.get();

 network.body.data.nodes.update(nodes.map(n => ({
   id:n.id,
   hidden: val==="all" ? false : n.group !== val
 })));
}

// RESET (FULL FIX)
function resetGraph(){
 let nodes = network.body.data.nodes.get();

 network.body.data.nodes.update(nodes.map(n => ({
   id:n.id,
   hidden:false,
   color:n.originalColor
 })));

 network.fit();
}

</script>
"""

html_data = html_data.replace("</body>", custom_ui + "</body>")

with open(html_path, "w", encoding="utf-8") as f:
    f.write(html_data)

print("🚀 Graph ready (ALL FIXED)")

# Open
try:
    subprocess.run(["open", html_path], check=False)
except:
    webbrowser.open(f"file://{html_path}")