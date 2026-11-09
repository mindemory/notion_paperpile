"""Read the Paperpile BibTeX export into clean, plain-text paper records.

Shared by the Notion sync (``paperpile_api.py``) and the library graph
(``build_graph.py``) so both agree on titles, author labels and venues.
"""
from __future__ import annotations

import codecs
import re
import unicodedata
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

import latexcodec  # noqa: F401  (registers the "ulatex" codec; ships with pybtex)
import pybtex.errors
from pybtex.database import BibliographyData, Person
from pybtex.database.input import bibtex

DEFAULT_BIB = Path(__file__).resolve().with_name("paperpile_references.bib")

# Where to look for a venue, in order of preference.
VENUE_FIELDS = ("journal", "booktitle", "publisher", "school", "institution")
TYPE_VENUES = {
    "phdthesis": "PhD thesis",
    "mastersthesis": "Master's thesis",
    "techreport": "Technical report",
    "unpublished": "Unpublished",
}
_LATEX_SYMBOLS = {
    "textquoteright": "\u2019",
    "textquoteleft": "\u2018",
    "textquotedblright": "\u201d",
    "textquotedblleft": "\u201c",
    "textendash": "\u2013",
    "textemdash": "\u2014",
}
_NOT_A_PERSON = {"others", "et al", "et al.", "anonymous"}

# Math that turns up in older exports: "$\alpha$-band", "The $\vartheta$-$\gamma$ neural code".
# Converted before the LaTeX decoder runs, which pairs up the "$" signs wrongly
# when it meets a command it doesn't know (it read "$-$" there as a minus sign).
_GREEK = dict(
    zip(
        "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi pi rho sigma tau upsilon phi chi psi omega".split(),
        "αβγδεζηθικλμνξπρστυφχψω",
    )
)
_MATH_SYMBOLS = {
    **_GREEK,
    **{name.capitalize(): letter.upper() for name, letter in _GREEK.items()},
    **{"var" + name: _GREEK[name] for name in ("epsilon", "theta", "phi", "rho", "sigma", "pi")},
    "pm": "±", "times": "×", "cdot": "·", "sim": "~", "approx": "≈",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "to": "→", "rightarrow": "→", "infty": "∞",
}
# Only $...$ holding a command counts as math, so "$0 to $10" in an abstract stays as it is.
_MATH = re.compile(r"(?<!\\)\$([^$]*\\[A-Za-z][^$]*)(?<!\\)\$")


def _math_to_text(match: re.Match) -> str:
    inner = re.sub(r"\\([A-Za-z]+)\s*", lambda m: _MATH_SYMBOLS.get(m.group(1), m.group(0)), match.group(1))
    return re.sub(r"[\^_]", "", inner)


def clean_latex(text: Optional[str]) -> str:
    """Turn a BibTeX field into readable plain text.

    ``{TMS} combined with {EEG}`` -> ``TMS combined with EEG``,
    ``Jean-R{\\'e}mi`` -> ``Jean-Rémi``, ``\\&`` -> ``&``.
    """
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"\\(?:url|path)\s*\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\href\s*\{[^}]*\}\s*\{([^}]*)\}", r"\1", s)
    s = _MATH.sub(_math_to_text, s)
    if "\\" in s or "$" in s:  # the decoder is slow; most fields only carry braces
        try:
            s = codecs.decode(s, "ulatex")
        except Exception:  # malformed LaTeX: fall back to stripping markup below
            pass
    s = re.sub(
        r"\\([A-Za-z]+)\s*(?:\{\})?",
        lambda m: _LATEX_SYMBOLS.get(m.group(1), ""),
        s,
    )
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    s = s.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def title_key(text: Optional[str]) -> str:
    """Normalise a title for duplicate detection.

    Case, accents, BibTeX braces, punctuation and spacing are ignored, so the
    raw Paperpile title, the cleaned title and whatever Notion stored all
    compare equal.
    """
    s = strip_accents(clean_latex(text)).casefold()
    s = re.sub(r"[^0-9a-z]+", " ", s)
    return s.strip()


@dataclass(frozen=True)
class Author:
    first: str  # given names, e.g. "Clayton E"
    last: str  # family name including particles, e.g. "van Ede"

    @property
    def display(self) -> str:
        """``Clayton E. Curtis`` (single-letter initials get a period)."""
        parts = []
        for token in self.first.split():
            if re.fullmatch(r"[A-Z]", token):
                token += "."
            parts.append(token)
        parts.append(self.last)
        return " ".join(p for p in parts if p)

    @property
    def is_person(self) -> bool:
        return self.last.casefold() not in _NOT_A_PERSON


def author_from_person(person: Person) -> Author:
    first = clean_latex(" ".join(person.first_names + person.middle_names))
    last = clean_latex(" ".join(person.prelast_names + person.last_names))
    if not last:  # e.g. a single-word name pybtex filed under first names
        first, last = "", first
    return Author(first=first, last=last)


def parse_year(value: Optional[str]) -> Optional[int]:
    match = re.search(r"\d{4}", value or "")
    return int(match.group()) if match else None


def format_label(authors: List[Author], year: Optional[int]) -> str:
    """The "Author, Year" label used as the Notion page title.

    ``Kay, 2018`` / ``Curtis & D'Esposito, 2003`` / ``Barbosa et al., 2020``
    """
    when = str(year) if year else "n.d."
    names = [a.last for a in authors if a.last]
    if not names:
        return "Unknown, " + when
    if len(names) == 1:
        return f"{names[0]}, {when}"
    if len(names) == 2:
        return f"{names[0]} & {names[1]}, {when}"
    return f"{names[0]} et al., {when}"


def pick_venue(entry) -> str:
    fields = entry.fields
    for name in VENUE_FIELDS:
        venue = clean_latex(fields.get(name))
        if venue:
            return venue
    if fields.get("archivePrefix"):
        return clean_latex(fields["archivePrefix"])
    how = clean_latex(fields.get("howpublished"))
    if how and not re.match(r"https?://", how):
        return how
    return TYPE_VENUES.get(entry.type.lower(), "")


def split_keywords(value: Optional[str]) -> List[str]:
    text = clean_latex(value)
    if not text:
        return []
    parts = text.split(";") if ";" in text else text.split(",")
    seen, out = set(), []
    for part in parts:
        kw = part.strip()
        if kw and kw.casefold() not in seen:
            seen.add(kw.casefold())
            out.append(kw)
    return out


@dataclass
class Paper:
    key: str
    entry_type: str
    title: str
    authors: List[Author]
    year: Optional[int]
    venue: str
    keywords: List[str] = field(default_factory=list)
    abstract: str = ""

    @property
    def label(self) -> str:
        return format_label(self.authors, self.year)

    @property
    def title_key(self) -> str:
        return title_key(self.title)


def paper_from_entry(key: str, entry) -> Paper:
    people = entry.persons.get("author") or entry.persons.get("editor") or []
    authors = [a for a in (author_from_person(p) for p in people) if a.is_person]
    return Paper(
        key=key,
        entry_type=entry.type.lower(),
        title=clean_latex(entry.fields.get("title")) or key,
        authors=authors,
        year=parse_year(entry.fields.get("year")),
        venue=pick_venue(entry),
        keywords=split_keywords(entry.fields.get("keywords")),
        abstract=clean_latex(entry.fields.get("abstract")),
    )


def load_bibliography(path: Path | str = DEFAULT_BIB) -> BibliographyData:
    """Parse a .bib file, tolerating the occasional malformed Paperpile entry."""
    try:
        return bibtex.Parser().parse_file(str(path))
    except Exception as exc:  # pybtex raises on e.g. repeated keys in strict mode
        warnings.warn(f"Strict BibTeX parse failed ({exc}); retrying leniently.")
        previous = pybtex.errors.strict
        pybtex.errors.set_strict_mode(False)
        try:
            return bibtex.Parser().parse_file(str(path))
        finally:
            pybtex.errors.set_strict_mode(previous)


def load_papers(path: Path | str = DEFAULT_BIB) -> List[Paper]:
    data = load_bibliography(path)
    return [paper_from_entry(key, entry) for key, entry in data.entries.items()]


def unique_by_title(papers: Iterable[Paper]) -> List[Paper]:
    """Drop repeated titles (Paperpile sometimes holds a preprint and its paper)."""
    seen, out = set(), []
    for paper in papers:
        if paper.title_key and paper.title_key not in seen:
            seen.add(paper.title_key)
            out.append(paper)
    return out
