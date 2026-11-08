from pathlib import Path

import pytest

import library

BIB = """
@ARTICLE{Curtis2003-ab,
  title    = "Persistent activity in the prefrontal cortex during working
              memory",
  author   = "Curtis, Clayton E and D'Esposito, Mark",
  journal  = "Trends Cogn. Sci.",
  volume   =  7,
  year     =  2003
}

@ARTICLE{Rafiei2022-xy,
  title    = "{TMS} Does Not Increase {BOLD} Activity at the Site of
              Stimulation",
  author   = "Rafiei, Farshad and Rahnev, Dobromir and Smith, Jane",
  journal  = "Neuroscience \\& Biobehavioral Reviews",
  keywords = "TMS; fMRI; concurrent TMS-fMRI",
  year     =  2022
}

@INPROCEEDINGS{Cline2021-bx,
  title     = "Advanced Artifact Removal for Automated {TMS}-{EEG} Data
               Processing",
  booktitle = "2021 10th International IEEE/EMBS Conference on Neural
               Engineering (NER)",
  author    = "Cline, Christopher C and Coogan, Carl and He, Bin",
  year      =  2021
}

@PHDTHESIS{Xing2014-iy,
  title  = "The role of early visual areas in human working memory",
  author = "Xing, Yuan",
  year   =  2014
}

@MISC{UnknownUnknown-rw,
  title        = "{IEEE} Xplore {Full-Text} {PDF}:",
  howpublished = "\\url{https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=\\&arnumber=9630489}",
  note         = "Accessed: 2025-1-1"
}

@ARTICLE{King2014-cd,
  title   = "Characterizing the dynamics of mental representations",
  author  = "King, J-R and Dehaene, S",
  journal = "Trends Cogn. Sci.",
  year    =  2014
}
"""


@pytest.fixture
def bib(tmp_path: Path) -> Path:
    path = tmp_path / "refs.bib"
    path.write_text(BIB)
    return path


@pytest.fixture
def papers(bib):
    return {p.key: p for p in library.load_papers(bib)}


def test_clean_latex():
    assert library.clean_latex("{TMS} combined with {EEG}") == "TMS combined with EEG"
    assert library.clean_latex("Neuroscience \\& Biobehavioral Reviews") == "Neuroscience & Biobehavioral Reviews"
    assert library.clean_latex("Jean-R{\\'e}mi") == "Jean-Rémi"
    assert library.clean_latex("Smith{\\textquoteright}s  law") == "Smith\u2019s law"
    assert library.clean_latex(None) == ""


def test_title_key_ignores_braces_case_and_punctuation():
    raw = "{TMS} Does Not Increase {BOLD} Activity: A Review"
    assert library.title_key(raw) == library.title_key("TMS does not increase BOLD activity - a review")
    assert library.title_key("Caf\\'{e} r{\\\"o}le") == library.title_key("Cafe role")
    assert library.title_key("Working memory") != library.title_key("Working memories")


def test_labels(papers):
    assert papers["Curtis2003-ab"].label == "Curtis & D'Esposito, 2003"
    assert papers["Rafiei2022-xy"].label == "Rafiei et al., 2022"
    assert papers["Xing2014-iy"].label == "Xing, 2014"
    # no author and no year used to crash the sync
    assert papers["UnknownUnknown-rw"].label == "Unknown, n.d."


def test_venues_never_borrow_from_the_previous_entry(papers):
    assert papers["Rafiei2022-xy"].venue == "Neuroscience & Biobehavioral Reviews"
    assert papers["Cline2021-bx"].venue.startswith("2021 10th International IEEE/EMBS Conference")
    assert papers["Xing2014-iy"].venue == "PhD thesis"
    assert papers["UnknownUnknown-rw"].venue == ""


def test_titles_are_plain_text(papers):
    assert papers["Rafiei2022-xy"].title == "TMS Does Not Increase BOLD Activity at the Site of Stimulation"
    assert papers["Curtis2003-ab"].title == "Persistent activity in the prefrontal cortex during working memory"


def test_keywords_and_authors(papers):
    assert papers["Rafiei2022-xy"].keywords == ["TMS", "fMRI", "concurrent TMS-fMRI"]
    king = papers["King2014-cd"].authors[0]
    assert (king.first, king.last) == ("J-R", "King")
    assert library.Author("Clayton E", "Curtis").display == "Clayton E. Curtis"


def test_unique_by_title():
    a = library.Paper("A1", "article", "{fMRI} of memory", [], 2020, "")
    b = library.Paper("A2", "article", "fMRI of Memory", [], 2021, "")
    assert [p.key for p in library.unique_by_title([a, b])] == ["A1"]


def test_repository_bibliography_parses():
    """The live Paperpile export must always parse; this guards the sync and the map."""
    papers = library.load_papers()
    assert len(papers) > 100
    assert all(p.label and p.title for p in papers)
