import json

import numpy as np

import build_graph as bg
import library


def make(key, title, authors, year=2020, abstract="", keywords=()):
    people = [library.Author(first, last) for last, first in authors]
    return library.Paper(key, "article", title, people, year, "J", list(keywords), abstract)


def test_author_variants_are_merged():
    papers = [
        make("a", "One", [("Curtis", "Clayton E"), ("Sprague", "Thomas C")]),
        make("b", "Two", [("Curtis", "Clayton"), ("D'Esposito", "Mark")]),
        make("c", "Three", [("Curtis", "C E"), ("Sprague", "T C")]),
    ]
    people, paper_people = bg.resolve_people(papers)
    curtis = [p for p in people if p["last"] == "Curtis"]
    assert len(curtis) == 1
    assert curtis[0]["name"] == "Clayton E. Curtis"
    assert sorted(curtis[0]["papers"]) == [0, 1, 2]
    assert len([p for p in people if p["last"] == "Sprague"]) == 1


def test_ambiguous_initials_follow_shared_coauthors():
    papers = [
        make("a", "One", [("Li", "Jian"), ("Wang", "Xiao-Jing")]),
        make("b", "Two", [("Li", "Jane"), ("Kay", "Kendrick")]),
        make("c", "Three", [("Li", "J"), ("Wang", "Xiao-Jing")]),
        make("d", "Four", [("Li", "J"), ("Nobody", "Else")]),
    ]
    people, paper_people = bg.resolve_people(papers)
    by_name = {p["name"]: p for p in people}
    assert sorted(by_name["Jian Li"]["papers"]) == [0, 2]
    assert by_name["Jane Li"]["papers"] == [1]
    assert by_name["J. Li"]["papers"] == [3]  # no shared co-authors: kept apart


def test_similar_papers_are_linked_and_clustered():
    wm = "working memory persistent activity prefrontal cortex delay period neurons"
    tms = "transcranial magnetic stimulation coil electric field cortex excitability"
    papers = [make(f"w{i}", f"Working memory study {i}", [("A", "B")], abstract=wm + f" item{i}") for i in range(6)]
    papers += [make(f"t{i}", f"Magnetic stimulation study {i}", [("C", "D")], abstract=tms + f" thing{i}") for i in range(6)]
    X, vocab, display = bg.topic_matrix(papers)
    assert np.allclose(np.linalg.norm(X, axis=1), 1, atol=1e-5)
    edges = bg.similarity_edges(X, k=3, min_sim=0.1)
    assert edges and all(a < b for a, b, _ in edges)
    assert all((a < 6) == (b < 6) for a, b, _ in edges)  # no link across the two topics
    labels = bg.communities(len(papers), edges, min_size=3)
    assert len(set(labels[:6])) == 1 and len(set(labels[6:])) == 1 and labels[0] != labels[6]
    names = bg.topic_labels(X, labels, vocab, display)
    assert any("memory" in n for n in names) and any("stimulation" in n for n in names)


def test_tiny_groups_are_unclustered():
    labels = bg.communities(5, [(0, 1, 1.0), (1, 2, 1.0), (3, 4, 1.0)], min_size=3)
    assert labels[:3] == [0, 0, 0] and labels[3:] == [-1, -1]


def test_coauthor_strength_discounts_big_consortia():
    edges = {(a, b): (n, w) for a, b, n, w in bg.coauthor_edges([[0, 1], [0, 1, 2, 3, 4, 5]], keep={0, 1, 2})}
    assert edges[(0, 1)][0] == 2
    assert edges[(0, 1)][1] == round(1 + 1 / 5, 3)
    assert edges[(0, 2)] == (1, 0.2)


def test_graph_data_and_page(tmp_path):
    papers = library.load_papers()[:150]
    data = bg.build_graph_data(papers)
    assert data["meta"]["papers"] == len(data["papers"])
    for a, b, w in data["paperLinks"]:
        assert 0 <= a < b < len(data["papers"]) and 0 < w <= 1.0001
    for p in data["papers"]:
        assert all(0 <= i < len(data["people"]) for i in p["authors"])
    html = bg.render_html(data)
    assert bg.DATA_MARKER not in html
    embedded = html.split('<script id="library-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert json.loads(embedded)["meta"] == data["meta"]


def test_script_tags_in_titles_cannot_break_the_page():
    data = {"meta": {}, "papers": [{"title": "</script><script>alert(1)</script>"}]}
    html = bg.render_html(data)
    embedded = html.split('<script id="library-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert json.loads(embedded)["papers"][0]["title"].startswith("</script>")
