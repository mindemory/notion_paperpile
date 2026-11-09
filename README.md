## Notion Paperpile Integration

Keeps a Notion reading list in step with a Paperpile library, and draws the library as an interactive map.

Paperpile exports the whole library as BibTeX to this repository (`paperpile_references.bib`) every time it changes. From there:

- **`paperpile_api.py`** adds every paper that isn't in the Notion database yet.
- **`build_graph.py`** turns the same file into **Library Map**, a page where papers are grouped by topic and authors by who they write with, in 2D or 3D.
- **GitHub Actions** (`.github/workflows/library.yml`) runs the tests, rebuilds the map and syncs Notion on every Paperpile update.

The current implementation is adapted for personal use but can be easily modified for any field of research.

### Setup

1. **Notion database.** Create a database with columns as desired. Here is a template for reference:
   <img width="1250" alt="Notion database template" src="https://user-images.githubusercontent.com/68425163/168459651-d8ade4c9-8091-4323-bd45-263eda57da2c.png">

   The sync fills three columns: `Author, Year` (the title column), `Title` and `Journal`. Every other column starts from the defaults in `newpage.json` (for example `Status: Unread`). If your columns are named differently, edit `newpage.json` and the three names at the top of `paperpile_api.py`.
2. **Notion integration.** Create one as described in https://developers.notion.com/docs/getting-started and share the database with it.
3. **Credentials.** Either set `NOTION_TOKEN` and `NOTION_DATABASE_ID` in the environment, or keep using a `notion_ids.csv` one directory above the repository (line 2: database id, line 3: secret).
4. **Paperpile export.** In Paperpile, turn on the BibTeX auto-export to this GitHub repository. Changes then show up here within minutes.
5. **Python packages.** `pip install -r requirements.txt` (Python 3.9 or newer).

### Sync Paperpile to Notion

```bash
python paperpile_api.py --dry-run   # list what would be added; writes nothing
python paperpile_api.py             # add the missing papers
```

Papers are matched on their title, ignoring case, accents, BibTeX braces (`{TMS}`) and punctuation, so re-running never adds the same paper twice. Entries without authors, years or a journal are handled, and requests back off when Notion rate-limits. As a safety net, the script refuses to add more than half the library to a database that already has entries (that usually means a renamed column); `--force` overrides it, and `--limit N` adds at most N papers.

Two one-off clean-ups for databases filled by older versions of the script. Both accept `--dry-run`, so you can see the changes first:

```bash
python paperpile_api.py --dedupe   # move repeated copies of a paper to Notion's trash
python paperpile_api.py --tidy     # "Smith et. al.,2020" -> "Smith et al., 2020", strip {braces}
```

`--dedupe` keeps the copy you have written notes, tags or a status on, in its columns or inside the page (or the oldest copy if none has any), and skips papers where more than one copy has notes. Anything it removes can be restored from Notion's Trash.

### Library Map

```bash
python build_graph.py   # writes site/index.html; open it in a browser
```

- **Papers by topic:** each paper is linked to the papers most similar to it (title, keywords and abstract), and the clusters those links form are labelled with their most distinctive terms.
- **Co-authors:** authors with at least two papers in the library, linked when they wrote together and grouped into the circles who publish together.
- Colour shows publication year (or an author's latest paper), and the histogram in the corner doubles as the key. Hover it to light up a year.
- Search any author or paper. Clicking an author on the topic map lights up all of their papers. Click any dot for details, similar papers, co-authors and a Google Scholar link.
- **2D**, **3D** and **List** views; the list is a sortable table of the current selection.
- The address remembers the view, so links can open it directly: `#authors` for the co-author map, `#3d` for the topic map in 3D, `#authors/3d` for both.

The page is a single HTML file with the data embedded. It loads its graph libraries from jsDelivr, so it needs an internet connection. It is built only from `paperpile_references.bib`, which is already public in this repository; nothing from Notion goes into it.

### Automation

The `Library` workflow runs on every push that touches the bibliography or the code:

| Job | What it does | Turn it on |
| --- | --- | --- |
| Tests | Runs `pytest`, including a check that the latest Paperpile export parses. | Always on. |
| Library map | Builds the map and attaches it to the run as the `library-map` artifact. On `main` it also publishes it to GitHub Pages. | Settings → Pages → Source: **GitHub Actions**. The map then lives at https://www.mindemory.io/notion_paperpile/ |
| Sync to Notion | Runs `python paperpile_api.py` on `main`, one run at a time. | Add `NOTION_TOKEN` and `NOTION_DATABASE_ID` under Settings → Secrets and variables → Actions. |

Until those are set up, the Pages and Notion steps skip with a notice instead of failing. To try the sync from GitHub first, open Actions → Library → Run workflow and tick "only list what would be added".

### Development

```bash
pip install -r requirements.txt pytest
python -m pytest
```

Happy Reading!!!
