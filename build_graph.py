"""Build an interactive map of the Paperpile library.

    python build_graph.py                  # writes site/index.html
    python build_graph.py --out map.html   # somewhere else
    python build_graph.py --json graph.json  # also dump the graph data

Two views of the same library, explorable in 2D or 3D in the browser:

* Papers: each paper is linked to the papers most similar to it (title,
  keywords and abstract, TF-IDF), and grouped into topic clusters.
* Authors: who wrote with whom, across the papers in the library, grouped
  into collaboration clusters.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import networkx as nx
import numpy as np
from networkx.algorithms import community as nx_community

import library

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "graph" / "template.html"
DEFAULT_OUT = HERE / "site" / "index.html"
DATA_MARKER = "/*__LIBRARY_DATA__*/"

SEED = 7
NEIGHBOURS = 6  # similarity links kept per paper
MIN_SIMILARITY = 0.12  # weaker matches are dropped (except a paper's single best match)
MIN_CLUSTER = 5  # smaller groups are shown as unclustered
MIN_AUTHOR_PAPERS = 2  # authors drawn in the co-author view

Edge = Tuple[int, int, float]

# --------------------------------------------------------------------------- people


def _norm(text: str) -> str:
    return library.strip_accents(text).casefold().replace(".", "").strip()


def _first_token(author: library.Author) -> str:
    tokens = _norm(author.first).split()
    return tokens[0] if tokens else ""


def _is_initials(token: str) -> bool:
    return bool(re.fullmatch(r"[a-z](?:-[a-z])*", token))


def _initials(token: str) -> str:
    return "-".join(part[:1] for part in token.split("-") if part)


def resolve_people(papers: Sequence[library.Paper]) -> Tuple[List[dict], List[List[int]]]:
    """Merge spelling variants of the same author across papers.

    "Curtis, Clayton E" and "Curtis, Clayton" are one person, and so is
    "Constantinidis, C" when only one Constantinidis with a C is in the
    library. When the initial is ambiguous ("Li, J" next to "Li, Jian" and
    "Li, Jane") it goes to whoever shares co-authors with that paper, or
    stays on its own.
    """
    occurrences = []  # (paper index, author, group key, first token)
    for pi, paper in enumerate(papers):
        for author in paper.authors:
            token = _first_token(author)
            group = (_norm(author.last), token[:1])
            occurrences.append((pi, author, group, token))

    groups: Dict[tuple, list] = defaultdict(list)
    for occ in occurrences:
        groups[occ[2]].append(occ)

    # co-author group keys per (group, full first name), for disambiguating initials
    paper_groups: Dict[int, set] = defaultdict(set)
    for pi, _, group, _ in occurrences:
        paper_groups[pi].add(group)

    person_of: Dict[tuple, Tuple] = {}  # (paper index, group) -> person id
    for group, occs in groups.items():
        full_names = Counter(tok for _, _, _, tok in occs if tok and not _is_initials(tok))
        if len(full_names) <= 1:
            for pi, _, _, _ in occs:
                person_of[(pi, group)] = group + ("*",)
            continue
        circles: Dict[str, set] = defaultdict(set)
        for pi, _, _, tok in occs:
            if tok in full_names:
                circles[tok] |= paper_groups[pi] - {group}
        for pi, _, _, tok in occs:
            if tok in full_names:
                person_of[(pi, group)] = group + (tok,)
                continue
            mine = paper_groups[pi] - {group}
            candidates = [name for name in full_names if not tok or _initials(name).startswith(tok)]
            best = max(candidates, key=lambda name: (len(circles[name] & mine), full_names[name]), default=None)
            if best is not None and circles[best] & mine:
                person_of[(pi, group)] = group + (best,)
            else:
                person_of[(pi, group)] = group + ("?" + tok,)

    ids: Dict[tuple, int] = {}
    variants: Dict[int, Counter] = defaultdict(Counter)
    written: Dict[int, set] = defaultdict(set)
    paper_people: List[List[int]] = [[] for _ in papers]
    for pi, author, group, _ in occurrences:
        pid = ids.setdefault(person_of[(pi, group)], len(ids))
        variants[pid][author] += 1
        if pi not in written[pid]:
            paper_people[pi].append(pid)
        written[pid].add(pi)

    people = []
    for pid in range(len(ids)):
        counts = variants[pid]
        best = max(counts, key=lambda a: (not _is_initials(_first_token(a)), counts[a], len(a.first)))
        mine = sorted(written[pid], key=lambda pi: (papers[pi].year or 0, papers[pi].label))
        people.append({"name": best.display, "last": best.last, "papers": mine})
    return people, paper_people


# --------------------------------------------------------------------------- topics

_STOPWORDS = set(
    """a about above across after again against all almost along already also although always am among an
    and another any are around as at be because been before being below between both but by can cannot
    could did do does doing done down during each either else enough especially etc even ever every few for
    from further had has have having he her here hers herself him himself his how however i ie eg if in into
    is it its itself just least less like made make makes many may me might more moreover most mostly much
    must my neither no nor not now of off often on once one only onto or other others otherwise our ours
    ourselves out over own per perhaps rather same several she should since so some such than that the their
    theirs them themselves then there thereby therefore these they this those though through throughout thus
    to together too toward towards under until up upon us use used uses using very via vs was we well were
    what whatever when where whereas whether which while who whom whose why will with within without would
    yet you your yours""".split()
)
# Words every abstract uses; they say nothing about the topic.
_GENERIC = set(
    """abstract al approach approaches article associated author authors based best better case cases
    change changes common compared comparison conclude conclusion conclusions consistent copyright current
    demonstrate demonstrated demonstrates describe described different difference differences directly due
    elsevier et evidence example examine examined examining finding findings first found four further general
    given greater group groups help highly identify identified important improve include included includes
    including increase increased increases increasing indicate indicated indicates inc investigate investigated
    investigation key known large larger license likely limited ltd main major manuscript new non novel number
    observed obtained order paper part particular possible potential present presented previous previously
    propose proposed provide provided provides published recent recently related relationship relative report
    reported reports research reserved respectively result results reveal revealed reveals review rights
    second set show showed shown shows significant significantly similar specific specifically still studies
    study suggest suggested suggesting suggests support supported three together two type types underlying
    understanding unique various versus way years ii iii iv""".split()
)
_SEGMENT = re.compile(r"[.,;:!?()\[\]{}\"“”‘’/|]+|\s[-–—]\s")
_WORD = re.compile(r"[a-z][a-z0-9\-]*[a-z0-9]|[a-z]")


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("sses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is", "ys")):
        return word[:-1]
    return word


def _terms(text: str, surfaces: Dict[str, Counter]) -> List[str]:
    """Unigrams and adjacent-word bigrams, lightly stemmed."""
    out: List[str] = []
    text = library.strip_accents(text).lower()
    for segment in _SEGMENT.split(text):
        words = _WORD.findall(segment)
        prev: Optional[Tuple[str, str]] = None
        for word in words:
            if len(word) < 2 or word in _STOPWORDS or word in _GENERIC or word.isdigit():
                prev = None
                continue
            stem = _stem(word)
            surfaces[stem][word] += 1
            out.append(stem)
            if prev:
                bigram = prev[0] + " " + stem
                surfaces[bigram][prev[1] + " " + word] += 1
                out.append(bigram)
            prev = (stem, word)
    return out


def topic_matrix(papers: Sequence[library.Paper]) -> Tuple[np.ndarray, List[str], List[str]]:
    """L2-normalised TF-IDF rows (title x3, keywords x2, abstract x1), the terms and their display forms."""
    surfaces: Dict[str, Counter] = defaultdict(Counter)
    docs: List[Counter] = []
    for paper in papers:
        counts: Counter = Counter()
        for term in _terms(paper.title, surfaces):
            counts[term] += 3
        for term in _terms(" ; ".join(paper.keywords), surfaces):
            counts[term] += 2
        for term in _terms(paper.abstract, surfaces):
            counts[term] += 1
        docs.append(counts)

    n = len(docs)
    df = Counter(term for doc in docs for term in doc)
    max_df = 0.5 * n
    vocab = sorted(
        t for t, d in df.items() if d <= max_df and d >= (3 if " " in t else 2)
    )
    index = {t: i for i, t in enumerate(vocab)}
    idf = np.array([math.log((1 + n) / (1 + df[t])) + 1 for t in vocab], dtype=np.float32)
    X = np.zeros((n, len(vocab)), dtype=np.float32)
    for row, doc in enumerate(docs):
        for term, count in doc.items():
            col = index.get(term)
            if col is not None:
                X[row, col] = 1 + math.log(count)
    X *= idf
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    X /= np.where(norms == 0, 1, norms)
    display = [surfaces[t].most_common(1)[0][0] for t in vocab]
    return X, vocab, display


def similarity_edges(X: np.ndarray, k: int = NEIGHBOURS, min_sim: float = MIN_SIMILARITY) -> List[Edge]:
    """Each paper's k most similar papers (cosine), as an undirected edge list."""
    n = X.shape[0]
    if n < 2:
        return []
    S = X @ X.T
    np.fill_diagonal(S, -1)
    edges: Dict[Tuple[int, int], float] = {}
    k = min(k, n - 1)
    for i in range(n):
        order = np.argpartition(-S[i], k - 1)[:k]
        order = order[np.argsort(-S[i, order], kind="stable")]
        for rank, j in enumerate(order):
            sim = float(S[i, j])
            if sim < min_sim and not (rank == 0 and sim >= 0.05):
                break
            a, b = (i, int(j)) if i < j else (int(j), i)
            edges[(a, b)] = max(edges.get((a, b), 0.0), sim)
    return [(a, b, round(w, 3)) for (a, b), w in sorted(edges.items())]


def communities(n: int, edges: Sequence[Edge], min_size: int = MIN_CLUSTER, resolution: float = 1.0) -> List[int]:
    """Cluster id per node (largest cluster first); -1 for nodes in tiny groups."""
    G = nx.Graph()
    G.add_nodes_from(range(n))
    G.add_weighted_edges_from(edges)
    if hasattr(nx_community, "louvain_communities"):
        groups = nx_community.louvain_communities(G, weight="weight", resolution=resolution, seed=SEED)
    else:  # networkx < 2.8
        groups = nx_community.greedy_modularity_communities(G, weight="weight")
    groups = sorted((sorted(g) for g in groups if len(g) >= min_size), key=lambda g: (-len(g), g[0]))
    labels = [-1] * n
    for cid, members in enumerate(groups):
        for node in members:
            labels[node] = cid
    return labels


def _overlap(a: str, b: str) -> bool:
    """Do two terms share a word? ("network" ~ "neural network", "pupil" ~ "pupillary")."""
    for x in a.split():
        for y in b.split():
            if x == y or x.startswith(y) or y.startswith(x) or (len(x) >= 7 and x[:7] == y[:7]):
                return True
    return False


def topic_labels(
    X: np.ndarray, clusters: Sequence[int], vocab: Sequence[str], display: Sequence[str], n_terms: int = 3
) -> List[str]:
    """Name each cluster by the terms that set it apart from the rest of the library."""
    names = []
    overall = X.mean(axis=0)
    for cid in range(max(clusters, default=-1) + 1):
        rows = [i for i, c in enumerate(clusters) if c == cid]
        score = X[rows].mean(axis=0) - overall
        chosen: List[int] = []
        for col in np.argsort(-score)[:30]:
            clash = [c for c in chosen if _overlap(vocab[col], vocab[c])]
            if clash:
                # prefer the longer phrase ("magnetic stimulation" over "stimulation", "magnetic")
                longer = len(vocab[col].split()) > max(len(vocab[c].split()) for c in clash)
                if longer and score[col] > 0.6 * max(score[c] for c in clash):
                    at = chosen.index(clash[0])
                    chosen = [c for c in chosen if c not in clash]
                    chosen.insert(min(at, len(chosen)), int(col))
                continue
            if len(chosen) < n_terms:
                chosen.append(int(col))
        names.append(" · ".join(display[c] for c in chosen))
    return names


def paper_terms(X: np.ndarray, vocab: Sequence[str], display: Sequence[str], n_terms: int = 4) -> List[List[str]]:
    out = []
    for row in X:
        picked: List[int] = []
        for col in np.argsort(-row)[: n_terms * 4]:
            if row[col] <= 0:
                break
            if any(_overlap(vocab[col], vocab[p]) for p in picked):
                continue
            picked.append(int(col))
            if len(picked) == n_terms:
                break
        out.append([display[c] for c in picked])
    return out


# --------------------------------------------------------------------------- authors


def coauthor_edges(paper_people: Sequence[Sequence[int]], keep: set) -> List[Tuple[int, int, int, float]]:
    """(a, b, papers together, collaboration strength) for authors in ``keep``.

    Strength is Newman's weighting: each shared paper adds 1/(n_authors - 1),
    so a 40-author consortium paper doesn't glue whole fields together.
    """
    together: Counter = Counter()
    strength: Counter = Counter()
    for members in paper_people:
        everyone = set(members)
        kept = sorted(p for p in everyone if p in keep)
        share = 1.0 / max(1, len(everyone) - 1)
        for i, a in enumerate(kept):
            for b in kept[i + 1:]:
                together[(a, b)] += 1
                strength[(a, b)] += share
    return [(a, b, together[(a, b)], round(strength[(a, b)], 3)) for (a, b) in sorted(together)]


# --------------------------------------------------------------------------- assemble


def build_graph_data(papers: Sequence[library.Paper], source: str = "paperpile_references.bib") -> dict:
    papers = library.unique_by_title(papers)
    people, paper_people = resolve_people(papers)

    X, vocab, display = topic_matrix(papers)
    paper_links = similarity_edges(X)
    paper_clusters = communities(len(papers), paper_links)
    cluster_names = topic_labels(X, paper_clusters, vocab, display)
    terms = paper_terms(X, vocab, display)

    shown = [i for i, person in enumerate(people) if len(person["papers"]) >= MIN_AUTHOR_PAPERS]
    shown_set = set(shown)
    author_links = coauthor_edges(paper_people, shown_set)
    position = {pid: i for i, pid in enumerate(shown)}
    local = communities(len(shown), [(position[a], position[b], w) for a, b, _, w in author_links], min_size=3)
    author_cluster = {pid: local[position[pid]] for pid in shown}
    author_cluster_names = []
    for cid in range(max(local, default=-1) + 1):
        members = sorted((pid for pid in shown if author_cluster[pid] == cid),
                         key=lambda pid: (-len(people[pid]["papers"]), people[pid]["name"]))
        author_cluster_names.append(" · ".join(people[pid]["last"] for pid in members[:2]))

    years = [p.year for p in papers if p.year]
    paper_rows = []
    for i, paper in enumerate(papers):
        paper_rows.append(
            {
                "key": paper.key,
                "title": paper.title,
                "label": paper.label,
                "year": paper.year,
                "venue": paper.venue,
                "authors": paper_people[i],
                "keywords": paper.keywords[:8],
                "terms": terms[i],
                "cluster": paper_clusters[i],
            }
        )
    people_rows = []
    for pid, person in enumerate(people):
        latest = max((papers[pi].year or 0 for pi in person["papers"]), default=0)
        people_rows.append(
            {
                "name": person["name"],
                "last": person["last"],
                "papers": person["papers"],
                "cluster": author_cluster.get(pid, -1),
                "year": latest or None,
            }
        )
    cluster_sizes = Counter(c for c in paper_clusters if c >= 0)
    author_sizes = Counter(author_cluster[pid] for pid in shown if author_cluster[pid] >= 0)
    return {
        "meta": {
            "built": dt.date.today().isoformat(),
            "source": source,
            "papers": len(papers),
            "people": len(people),
            "years": [min(years), max(years)] if years else None,
            "minAuthorPapers": MIN_AUTHOR_PAPERS,
        },
        "papers": paper_rows,
        "people": people_rows,
        "paperLinks": [list(e) for e in paper_links],
        "authorLinks": [[a, b, n, w] for a, b, n, w in author_links],
        "paperClusters": [{"label": name, "size": cluster_sizes[i]} for i, name in enumerate(cluster_names)],
        "authorClusters": [{"label": name, "size": author_sizes[i]} for i, name in enumerate(author_cluster_names)],
    }


def render_html(data: dict, template: Path = TEMPLATE) -> str:
    page = template.read_text(encoding="utf-8")
    if DATA_MARKER not in page:
        raise ValueError(f"{template} has no {DATA_MARKER} placeholder")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\!--")
    return page.replace(DATA_MARKER, payload)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bib", type=Path, default=library.DEFAULT_BIB, help="BibTeX file (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="HTML file to write (default: %(default)s)")
    parser.add_argument("--json", type=Path, help="also write the graph data as JSON")
    args = parser.parse_args(argv)

    papers = library.load_papers(args.bib)
    data = build_graph_data(papers, source=args.bib.name)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_html(data), encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = data["meta"]
    print(
        f"Wrote {args.out}: {meta['papers']} papers in {len(data['paperClusters'])} topic clusters, "
        f"{meta['people']} authors ({sum(1 for p in data['people'] if len(p['papers']) >= MIN_AUTHOR_PAPERS)} "
        f"with {MIN_AUTHOR_PAPERS}+ papers) in {len(data['authorClusters'])} collaboration clusters."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
