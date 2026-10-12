
# KG_demo: Building Interactive Knowledge Graphs from Scientific Literature and Encyclopedias

**KG_demo** is a Python-based demonstration repository for constructing interactive knowledge graphs from encyclopedia HTML and scientific literature. It explores different ways of connecting scientific concepts and visualising their relationships in a browser.

The repository includes three scripts for comparing encyclopedia-link-based graph construction with approaches that combine encyclopedia concepts and scientific-text evidence.

## Repository scripts

| Script | Purpose |
|---|---|
| [`newKG_encyclopedia.py`](https://github.com/rkclimate20/KG_demo/blob/main/newKG_encyclopedia.py) | Builds a graph from links between encyclopedia entries, with an interactive visualisation and node highlighting. |
| [`build_semantic_kg.py`](https://github.com/rkclimate20/KG_demo/blob/main/build_semantic_kg.py) | Builds a richer graph combining encyclopedia concepts and scientific-text evidence, with optional Wikipedia links and paper metadata. |

These scripts are alternative approaches. Choose the one that best matches your input data and research objective.

## Features

- Extracts encyclopedia terms from HTML entries marked with `role="ami_entry"`.
- Retains Wikipedia URLs and can add links between encyclopedia terms.
- Detects term co-occurrence in the same or adjacent sentences in scientific text.
- Creates hierarchy links between longer phrases and shorter terms they contain.
- Detects abbreviation patterns such as `sea surface temperature (SST)`.
- Retains supporting sentences for selected scientific-text relationships.
- Produces interactive browser-based network visualisations using PyVis.
- Uses NetworkX for graph construction and, in the main script, community analysis.
- Exports node and edge CSV tables; the main script also exports paper metadata.
- Optionally adds phrases from a merged keyphrase CSV.

## How it works

The paper-aware workflow generally follows these steps:

1. Read the encyclopedia HTML and extract terms, links and Wikipedia URLs.
2. Normalise terms to reduce straightforward case and plural variants.
3. Read plain-text scientific papers when a text directory is supplied.
4. Identify co-occurrence, hierarchy and abbreviation relationships.
5. Optionally add Wikipedia page-link relationships.
6. Build the graph with NetworkX.
7. Generate an interactive HTML visualisation and export tables.

### Relationship types

| Relationship | Meaning |
|---|---|
| `cooc` | Terms occur in the same sentence or in adjacent sentences. |
| `hierarchy` | A longer, more specific phrase contains a shorter term. |
| `abbrev` | An abbreviation is associated with its expanded form. |
| `wiki` | Two encyclopedia terms are connected by a Wikipedia page link. |

These are heuristic or structural relationships. Co-occurrence does not, by itself, establish causation or a formally defined semantic relation.

## Requirements

The scripts use Python and these packages:

- Beautiful Soup 4
- NetworkX
- PyVis
- Pandas

Install the dependencies:

```bash
python -m pip install beautifulsoup4 networkx pyvis pandas
```

Python 3.10 or later is a reasonable starting point; use a version compatible with your environment and dependencies.

## Getting started

### 1. Clone the repository

```bash
git clone https://github.com/rkclimate20/KG_demo.git
cd KG_demo
```

### 2. Prepare the input files

The encyclopedia HTML should contain entries in the expected structure, for example:

```html
<div role="ami_entry" term="Marine heatwave">
    ...
</div>
```

For paper-aware graph construction, prepare a directory containing scientific papers as plain-text `.txt` files. You can extract this text from full-text XML or another supported source before running the graph-building script.

Example layout (paths are illustrative):

```text
project/
├── KG_demo/
│   ├── build_semantic_kg.py
│   └── newKG_encyclopedia.py
├── encyclopediaTest.html
└── text_all/
    ├── paper1.txt
    ├── paper2.txt
    └── paper3.txt
```

### 3. Build a graph with the main script

Run this command from the parent directory of `KG_demo`:

```bash
python KG_demo/build_semantic_kg.py --input encyclopediaTest.html --output semantic_KG.html --text-dir text_all
```

To include Wikipedia page-link relationships, add `--wiki-links`:

```bash
python KG_demo/build_semantic_kg.py --input encyclopediaTest.html --output semantic_KG.html --text-dir text_all --wiki-links
```

If `--text-dir` is omitted, paper-text-based co-occurrence and abbreviation relationships are skipped. Other applicable relationships, such as term hierarchy, may still be created.

### 4. Run the encyclopedia-link script

The legacy script uses input and output paths configured inside the Python file. Open `newKG_encyclopedia.py` and edit the paths to match your environment, then run:

```bash
python KG_demo/newKG_encyclopedia.py
```

This script is a simpler baseline focused on links between encyclopedia entries.

## Command-line options

The following options are available in `build_semantic_kg.py` . Check the help output for the exact options supported by the version you are using:

```bash
python KG_demo/build_semantic_kg.py --help
```

| Option | Description |
|---|---|
| `--input`, `-i` | Path to the encyclopedia HTML file. |
| `--output`, `-o` | Path for the interactive HTML graph. |
| `--text-dir`, `-t` | Directory containing scientific-text `.txt` files. |
| `--papers-dir`, `-p` | In the main script, directory containing downloaded paper records and metadata. |
| `--wiki-links` | Adds Wikipedia page-link relationships. |
| `--no-cooccurrence` | Disables co-occurrence relationships. |
| `--no-hierarchy` | Disables term-hierarchy relationships. |
| `--no-abbreviations` | Disables abbreviation detection. |
| `--min-cooc` | Minimum co-occurrence weight; default is `1.0`. |
| `--top-k` | Maximum number of strongest co-occurrence links retained per term; default is `6`. |
| `--max-fanout` | Limits hierarchy links to very generic terms; default is `40`. |
| `--extra-noise` | Comma-separated list of additional terms to exclude. |
| `--merged-csv` | Optional merged keyphrase CSV. |
| `--add-merged-phrases` | Adds eligible phrases from the merged CSV to the graph vocabulary. |
| `--min-phrase-count` | Minimum frequency for added phrases when a suitable count column is available; default is `3`. |
| `--edges-csv` | Optional output path for the edge CSV. |
| `--nodes-csv` | Optional output path for the node CSV. |

## Output files

The main script generates an interactive HTML graph and CSV tables.

| Output | Contents |
|---|---|
| `semantic_KG.html` | Interactive network visualisation. |
| `semantic_KG_nodes.csv` | Node labels, mention counts, paper counts, degree/connectivity, Wikipedia URL and associated paper IDs, where available. |
| `semantic_KG_edges.csv` | Source and target terms, relationship types, weights and supporting evidence, where available. |
| `semantic_KG_papers.csv` | Available paper identifiers, titles, authors, publication years and links. |

Output CSV names are derived from the HTML output path. The node and edge CSV paths can be overridden using `--nodes-csv` and `--edges-csv`. The alternative paper-aware script exports node and edge tables but does not provide the same paper-metadata table as the main script.

## Exploring the graph

Open the generated HTML file in a modern web browser. Depending on the script, you can search for terms, highlight neighbouring nodes, zoom and pan around the graph, inspect relationships, and follow available Wikipedia links. The richer graph may also expose relationship filters, topic communities and supporting sentences.

Some visualisation resources may be loaded remotely, so an internet connection may be needed for all interactive features.

## Potential applications

- Exploring climate-science reports and research articles.
- Connecting encyclopedia concepts with scientific-text evidence.
- Examining terminology, abbreviations and contextual relationships.
- Supporting literature exploration and topic discovery.
- Prototyping semantic knowledge extraction workflows.

## Related projects

- [semanticClimate](https://github.com/semanticClimate) — open tools and resources for extracting knowledge from scientific literature.
- [pygetpapers](https://github.com/petermr/pygetpapers) — searching and downloading scholarly literature.
- [txt2phrases](https://github.com/semanticClimate/txt2phrases) — text processing and keyphrase workflows.
- [encyclopedia](https://github.com/semanticClimate/encyclopedia) — encyclopedia generation from scientific and climate-related material.

## License

Apache License, Version 2.0

## Acknowledgements

This project uses open-source tools including Beautiful Soup, NetworkX, Pandas and PyVis.
