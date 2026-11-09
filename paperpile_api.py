"""Sync the Paperpile BibTeX export into the Notion reading list.

    python paperpile_api.py --dry-run   # list what would be added; changes nothing
    python paperpile_api.py             # add every paper that is missing from Notion
    python paperpile_api.py --dedupe    # trash repeated copies of a paper (keeps your notes)
    python paperpile_api.py --tidy      # one-off clean-up of rows from older versions

Credentials come from the NOTION_TOKEN and NOTION_DATABASE_ID environment
variables or, failing that, from ../notion_ids.csv (line 2: database id,
line 3: secret). Papers are matched on their title, ignoring case, accents,
BibTeX braces and punctuation, so re-running never creates duplicates.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import library
from notion_api import NotionClient, NotionError, plain_text, text_value

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "newpage.json"
LEGACY_CREDENTIALS = HERE.parent / "notion_ids.csv"

# Column names in the Notion database. Rename here (and in newpage.json) if yours differ.
LABEL_PROP = "Author, Year"  # the database's title column
TITLE_PROP = "Title"
VENUE_PROP = "Journal"

# Labels written by the first version of this script: "Smith et. al.,2020".
LEGACY_LABEL = re.compile(r"^(?P<who>.+?) et\. al\.,\s*(?P<when>.+)$")


def load_credentials(path: Optional[Path] = None) -> Tuple[str, str]:
    """Return (token, database_id)."""
    token = os.environ.get("NOTION_TOKEN", "").strip()
    database_id = os.environ.get("NOTION_DATABASE_ID", "").strip()
    if token and database_id:
        return token, database_id
    path = Path(path) if path else LEGACY_CREDENTIALS
    if not path.exists():
        raise SystemExit(
            "No Notion credentials found. Set NOTION_TOKEN and NOTION_DATABASE_ID, "
            f"or create {path} (line 2: database id, line 3: secret)."
        )
    lines = path.read_text().splitlines()
    if len(lines) < 3 or not lines[1].strip() or not lines[2].strip():
        raise SystemExit(f"{path} should hold the database id on line 2 and the secret on line 3.")
    return lines[2].strip(), lines[1].strip()


def load_template(path: Path = TEMPLATE) -> Dict[str, Any]:
    with open(path) as f:
        template = json.load(f)
    for prop in (LABEL_PROP, TITLE_PROP, VENUE_PROP):
        if prop not in template.get("properties", {}):
            raise SystemExit(f"{path} has no '{prop}' property; update the column names in paperpile_api.py.")
    return template


def build_page(template: Dict[str, Any], database_id: str, paper: library.Paper) -> Dict[str, Any]:
    """The request body that creates ``paper`` as a page in the database."""
    page = copy.deepcopy(template)
    page["parent"] = {"database_id": database_id}
    props = page["properties"]
    props[LABEL_PROP] = {"title": text_value(paper.label)}
    props[TITLE_PROP] = {"rich_text": text_value(paper.title)}
    props[VENUE_PROP] = {"rich_text": text_value(paper.venue)}
    return page


@dataclass
class Plan:
    pages: List[Dict[str, Any]]
    missing: List[library.Paper]
    duplicate_titles: List[Tuple[str, int]] = field(default_factory=list)
    not_in_paperpile: int = 0


def plan_sync(pages: List[Dict[str, Any]], papers: List[library.Paper]) -> Plan:
    """Work out which papers still need a Notion page."""
    keys = [library.title_key(plain_text(p.get("properties", {}).get(TITLE_PROP))) for p in pages]
    if pages and not any(keys):
        raise SystemExit(
            f"None of the {len(pages)} Notion pages has a '{TITLE_PROP}' value. "
            "Was the column renamed? Stopping so nothing gets duplicated."
        )
    existing = set(k for k in keys if k)
    counts = Counter(k for k in keys if k)
    shown = {}
    for page, key in zip(pages, keys):
        if counts.get(key, 0) > 1 and key not in shown:
            shown[key] = plain_text(page["properties"].get(TITLE_PROP))
    duplicates = [(shown[k], counts[k]) for k in shown]

    missing, seen = [], set(existing)
    for paper in papers:
        key = paper.title_key
        if key and key not in seen:
            seen.add(key)
            missing.append(paper)
    library_keys = {p.title_key for p in papers}
    stray = sum(1 for k in existing if k not in library_keys)
    return Plan(pages=pages, missing=missing, duplicate_titles=duplicates, not_in_paperpile=stray)


def has_user_data(page: Dict[str, Any], template: Dict[str, Any]) -> bool:
    """True if any column besides the synced ones differs from a fresh page.

    That covers notes, tags, dates and a Status other than the template's
    default ("Unread"): anything you typed into Notion yourself.
    """
    defaults = template.get("properties", {})
    for name, value in page.get("properties", {}).items():
        if name in (LABEL_PROP, TITLE_PROP, VENUE_PROP) or not isinstance(value, dict):
            continue
        kind = value.get("type")
        data = value.get(kind)
        if kind in ("title", "rich_text"):
            if plain_text(value).strip():
                return True
        elif kind in ("select", "status"):
            default = (defaults.get(name, {}).get(kind) or {}).get("name")
            if data and data.get("name") != default:
                return True
        elif kind in ("formula", "rollup", "created_time", "created_by", "last_edited_time", "last_edited_by", "unique_id"):
            continue  # computed by Notion, not entered by hand
        elif data not in (None, "", [], False):
            return True
    return False


def plan_dedupe(
    pages: List[Dict[str, Any]],
    template: Dict[str, Any],
    has_content: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Tuple[List[Dict[str, Any]], List[List[Dict[str, Any]]]]:
    """Extra copies of the same paper to archive, and groups that need a human.

    In each group of rows sharing a title, keep the copy that has your notes
    (filled-in columns, or anything written inside the page when ``has_content``
    is given), or the oldest copy if none has. Groups where several copies
    carry notes are left alone and reported instead.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for page in pages:
        key = library.title_key(plain_text(page.get("properties", {}).get(TITLE_PROP)))
        if key:
            groups.setdefault(key, []).append(page)
    to_archive, conflicts = [], []
    for group in groups.values():
        if len(group) < 2:
            continue
        annotated = [
            p for p in group if has_user_data(p, template) or (has_content is not None and has_content(p))
        ]
        if len(annotated) > 1:
            conflicts.append(group)
            continue
        keeper = annotated[0] if annotated else min(group, key=lambda p: p.get("created_time", ""))
        to_archive.extend(p for p in group if p is not keeper)
    return to_archive, conflicts


def tidy_updates(page: Dict[str, Any]) -> Dict[str, Any]:
    """Property updates that bring an older row in line with the current format."""
    props = page.get("properties", {})
    updates: Dict[str, Any] = {}
    label = plain_text(props.get(LABEL_PROP))
    match = LEGACY_LABEL.match(label)
    if match:
        updates[LABEL_PROP] = {"title": text_value(f"{match['who']} et al., {match['when'].strip()}")}
    for name in (TITLE_PROP, VENUE_PROP):
        old = plain_text(props.get(name))
        new = library.clean_latex(old)
        if old and new and new != old:
            updates[name] = {"rich_text": text_value(new)}
    return updates


def run_sync(
    client: NotionClient,
    database_id: str,
    papers: List[library.Paper],
    template: Dict[str, Any],
    dry_run: bool = False,
    limit: Optional[int] = None,
    force: bool = False,
    log: Callable[[str], None] = print,
) -> int:
    pages = client.query_database(database_id)
    log(f"There are {len(pages)} existing entries on Notion and {len(papers)} in Paperpile.")
    plan = plan_sync(pages, papers)
    if plan.duplicate_titles:
        log(
            f"Note: {len(plan.duplicate_titles)} titles appear more than once on Notion "
            "(see `python paperpile_api.py --dedupe --dry-run`), e.g.:"
        )
        for title, n in plan.duplicate_titles[:5]:
            log(f"    {n}x  {title}")
    if plan.not_in_paperpile:
        log(f"Note: {plan.not_in_paperpile} Notion entries are no longer in Paperpile (left untouched).")

    log(f"{len(plan.missing)} papers are missing from Notion.")
    if (
        not force
        and len(pages) >= 20
        and len(plan.missing) > 0.5 * max(1, len(papers))
    ):
        log(
            "Refusing to add more than half the library to a database that already has entries; "
            "this usually means the Title column changed. Re-run with --force if this is intended."
        )
        return 2
    todo = plan.missing if limit is None else plan.missing[: max(0, limit)]
    if dry_run:
        for paper in todo:
            log(f"  would add  {paper.label}: {paper.title}")
        log("Dry run: nothing was written to Notion.")
        return 0

    created, failed = 0, 0
    for paper in todo:
        try:
            client.create_page(build_page(template, database_id, paper))
        except NotionError as exc:
            failed += 1
            log(f"  FAILED     {paper.label}: {paper.title} ({exc})")
            continue
        created += 1
        log(f"  added      {paper.label}: {paper.title}")
    log(f"Success: {created} new entries were added to Notion!")
    if failed:
        log(f"{failed} entries could not be added; re-running will retry them.")
        return 1
    return 0


def run_tidy(
    client: NotionClient,
    database_id: str,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> int:
    pages = client.query_database(database_id)
    work = [(page, tidy_updates(page)) for page in pages]
    work = [(page, upd) for page, upd in work if upd]
    by_column = Counter(name for _, upd in work for name in upd)
    log(f"{len(work)} of {len(pages)} Notion entries can be tidied {dict(by_column)}.")
    for page, upd in work[:10]:
        before = plain_text(page["properties"].get(LABEL_PROP))
        log(f"  e.g. {before!r}: " + ", ".join(f"{k} -> {plain_text(v)!r}" for k, v in upd.items()))
    if dry_run:
        log("Dry run: nothing was written to Notion.")
        return 0
    failed = 0
    for page, upd in work:
        try:
            client.update_page(page["id"], upd)
        except NotionError as exc:
            failed += 1
            log(f"  FAILED {plain_text(page['properties'].get(LABEL_PROP))}: {exc}")
    log(f"Tidied {len(work) - failed} entries.")
    return 1 if failed else 0


def run_dedupe(
    client: NotionClient,
    database_id: str,
    template: Dict[str, Any],
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> int:
    pages = client.query_database(database_id)
    log("Checking repeated entries for notes written inside the page...")
    to_archive, conflicts = plan_dedupe(pages, template, has_content=lambda p: client.page_has_content(p["id"]))
    log(f"{len(to_archive)} duplicate entries can be moved to Notion's trash (out of {len(pages)}).")
    for page in to_archive:
        props = page["properties"]
        log(f"  {plain_text(props.get(LABEL_PROP))}: {plain_text(props.get(TITLE_PROP))} (added {page.get('created_time', '?')[:10]})")
    for group in conflicts:
        props = group[0]["properties"]
        log(f"  SKIPPED (several copies have notes; merge by hand): {plain_text(props.get(TITLE_PROP))}")
    if dry_run:
        log("Dry run: nothing was written to Notion.")
        return 0
    failed = 0
    for page in to_archive:
        try:
            client.archive_page(page["id"])
        except NotionError as exc:
            failed += 1
            log(f"  FAILED {plain_text(page['properties'].get(LABEL_PROP))}: {exc}")
    log(f"Moved {len(to_archive) - failed} duplicates to the trash; restore them from Notion's Trash if needed.")
    return 1 if failed else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing to Notion")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tidy", action="store_true", help="rewrite old 'X et. al.,YYYY' labels and BibTeX braces in existing rows")
    mode.add_argument("--dedupe", action="store_true", help="move extra copies of the same paper to Notion's trash")
    parser.add_argument("--limit", type=int, help="add at most this many pages")
    parser.add_argument("--force", action="store_true", help="skip the mass-duplication safety check")
    parser.add_argument("--bib", type=Path, default=library.DEFAULT_BIB, help="BibTeX file (default: %(default)s)")
    parser.add_argument("--template", type=Path, default=TEMPLATE, help="page template (default: %(default)s)")
    parser.add_argument("--credentials", type=Path, help="credentials file (default: ../notion_ids.csv)")
    args = parser.parse_args(argv)

    token, database_id = load_credentials(args.credentials)
    client = NotionClient(token)
    try:
        if args.tidy:
            return run_tidy(client, database_id, dry_run=args.dry_run)
        template = load_template(args.template)
        if args.dedupe:
            return run_dedupe(client, database_id, template, dry_run=args.dry_run)
        papers = library.load_papers(args.bib)
        return run_sync(client, database_id, papers, template, dry_run=args.dry_run, limit=args.limit, force=args.force)
    except NotionError as exc:
        print(f"Notion request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
