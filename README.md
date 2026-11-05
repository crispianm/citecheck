# citecheck

Check your BibTeX file against DBLP, offline, and catch wrong years, venues, authors and stale arXiv citations before a reviewer does.

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/crispianm/citecheck/main/install.sh | bash
```

The installer finds Python 3.9+, clones citecheck to `~/.citecheck`, puts a `citecheck` command in `~/.local/bin`, and then walks you through building an offline DBLP index (a few GB, one-off). Re-running it is safe. Pass options after `bash -s --`, for example `... | bash -s -- --yes --index lite`.

Prefer pipx? `pipx install git+https://github.com/crispianm/citecheck`, then `citecheck setup`.

Requires Python 3.9 or newer built with SQLite FTS5 (almost all are). No other Python packages. Linux and macOS; on Windows use WSL.

## 30-second example

A real run on the test fixture bundled in the repo:

```console
$ citecheck check tests/fixtures/refs_fixture.bib
citecheck: tests/fixtures/refs_fixture.bib
10 entries: 2 mismatch, 1 published, 4 manual, 3 ok

== Mismatch (2) ==
  wrongyear  [mismatch]  via dblp
    - year: bib 2019 vs 2017
    record: https://dblp.org/rec/conf/nips/VaswaniSPUJGKP17
    suggested:
      @inproceedings{wrongyear,
        author    = {Ashish Vaswani and Noam Shazeer and Niki Parmar},
        title     = {Attention is All you Need},
        booktitle = {NeurIPS},
        pages     = {5998--6008},
        year      = {2017}
      }
  wrongauthor  [mismatch]  via dblp
    - authors: #1 bib 'Smyth' vs 'Chen' (record: Wei Chen; Li Wang)
    record: https://dblp.org/rec/conf/aaai/Chen22
    suggested:
      @inproceedings{wrongauthor,
        author    = {Wei Chen and Li Wang},
        title     = {Graph Neural Networks for Fixture Traffic Forecasting},
        booktitle = {AAAI},
        pages     = {1000--1008},
        year      = {2022}
      }

== Published version exists (1) ==
  arxivcite  [published]  via dblp
    - venue: cited as arXiv, published at CVPR (CVPR)
    record: https://dblp.org/rec/conf/cvpr/HeZRS16
    suggested:
      @inproceedings{arxivcite,
        author    = {Kaiming He and Xiangyu Zhang and Shaoqing Ren and Jian Sun},
        title     = {Deep Residual Learning for Image Recognition},
        booktitle = {CVPR},
        pages     = {770--778},
        year      = {2016},
        doi       = {10.1109/cvpr.2016.90}
      }

== Needs manual check (4) ==
  ...
== Duplicates ==
  same title: vaswani_ok / wrongyear
  same title: he16 / arxivcite

$ echo $?
1
```

Entries print with the key, what differs, and a link to the record. Add `--write-fixed` to get a corrected `refs.fixed.bib`.

## What it checks

Each entry gets one status:

| status | meaning |
|---|---|
| `ok` | matches a record in DBLP (or an online source) |
| `note` | matches, with accepted differences (abbreviated venue, page style) or lint advice |
| `mismatch` | year, authors, title or venue disagree with the record |
| `manual` | no confident match anywhere; look at it yourself |
| `published` | cited as an arXiv preprint, but a venue version now exists |
| `retracted` | the DOI is marked retracted (OpenAlex, Crossref notices) |

Lint notes (switch off with `--no-lint`): required fields missing for the entry type, acronyms and proper nouns in titles without `{}` protection, the same venue spelled several ways, "et al." inside an author field, arXiv papers cited as `@article` with no eprint id, DOIs written as URLs.

Across the project it also reports cited keys missing from the `.bib`, entries never cited, and duplicate keys or titles.

## Usage

```bash
citecheck check refs.bib                 # every entry in one file
citecheck check paper/                   # a LaTeX project: finds .bib, .aux/.tex, checks cited keys only
citecheck check refs.bib --aux main.aux  # take cited keys from this .aux/.tex/dir
citecheck check refs.bib --all           # ignore citations, check everything
citecheck check refs.bib --only vaswani2017attention smith2020fast
citecheck check paper/ --out report      # writes report/report.md, report.csv, report.html
citecheck check refs.bib --write-fixed   # writes refs.fixed.bib; never overwrites your .bib
citecheck check refs.bib --offline       # no network: index and cache only
citecheck check refs.bib --no-s2         # skip Semantic Scholar
citecheck check refs.bib --strict        # `manual` entries also fail
citecheck check refs.bib --db other.sqlite
citecheck search "attention is all you need"
citecheck stats                          # what is in your index
citecheck update                         # refresh the index (and the tool)
citecheck doctor                         # diagnose the installation
```

**Exit codes:** `0` clean; `1` any `mismatch`, `retracted`, or cited key missing from the `.bib` (with `--strict`, also any `manual`).

## CI, pre-commit, GitHub Action

An index is large, so CI usually runs without one: `--offline` with a cache, or the online sources only (`citecheck setup --index none`).

```yaml
# .github/workflows/refs.yml
name: refs
on: [push, pull_request]
jobs:
  citecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: curl -fsSL https://raw.githubusercontent.com/crispianm/citecheck/main/install.sh | bash -s -- --yes --index none
      - run: ~/.local/bin/citecheck check . --strict --out refs-report
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: refs-report, path: refs-report}
```

Pre-commit (local hook, with a local index):

```yaml
repos:
  - repo: local
    hooks:
      - id: citecheck
        name: citecheck
        entry: citecheck check . --offline
        language: system
        files: \.(bib|tex)$
        pass_filenames: false
```

## The offline DBLP index

DBLP publishes its whole database as one XML file. `citecheck setup` downloads it, verifies the checksum, and builds a local SQLite search index, so lookups take milliseconds and never hit a rate limit.

| | download | index on disk | peak disk | build time |
|---|---|---|---|---|
| **full** | ~1.1 GB | ~5.5 GB | ~7 GB | ~6–12 min |
| **lite** | ~1.1 GB | ~5.0 GB | ~6.5 GB | ~6–12 min |
| **none** | - | - | - | - |

*Full* keeps every record type. *Lite* keeps papers, theses and datasets only. *None* skips the index and uses the online sources, which is slower and rate-limited. Build time depends on your CPU and disk; setup shows a progress bar with an ETA.

The download uses [aria2](https://aria2.github.io) when installed (`apt install aria2`, `brew install aria2`): 8 parallel connections, and resumable if interrupted. Otherwise it falls back to `curl -C -`, also resumable. Re-run `citecheck setup` and it picks up where it stopped.

**Updating.** DBLP regenerates the dump regularly; refreshing monthly is plenty:

```bash
citecheck update
```

It skips the download if the remote checksum is unchanged, and rebuilds incrementally (only new or changed records). If you deleted the `.gz` to save space, `update` downloads it again.

## Coverage

DBLP covers computer science. For anything it does not know (biology, physics, medicine, humanities), citecheck falls back to Semantic Scholar, Crossref and OpenAlex online. Those results are marked in the `source` field of the report. Entries that match nowhere are `manual`.

## FAQ

**Why not use the DBLP search API?** It sits behind a bot check and rate limits, which is unfriendly to bulk checking. The official dump is the supported way.

**Windows?** Use WSL and run the installer there. Native Windows is untested.

**Where does everything live?** Code in `~/.citecheck`; the command in `~/.local/bin/citecheck`; config in `~/.config/citecheck/config`; the index, download and API caches in `~/.local/share/citecheck` (or the directory you chose). `XDG_CONFIG_HOME` and `XDG_DATA_HOME` are honored; `CITECHECK_HOME` moves the code, and `CITECHECK_DB` points at a different index.

**Can I see what the installer does first?** Yes: read [`install.sh`](install.sh), or download it, then run it.

**Can I skip prompts?** `citecheck setup --yes`. Defaults: full index if the disk has room, else lite, else none.

**Uninstall?**
```bash
curl -fsSL https://raw.githubusercontent.com/crispianm/citecheck/main/install.sh | bash -s -- --uninstall
```
This removes the command and the clone, and asks before deleting the data directory. A `PATH` line the installer added to your shell rc is left alone.

**Does it ever edit my .bib?** Never. `--write-fixed` writes a separate file and refuses to overwrite an existing one without `--force`.

## Data sources and licenses

- [DBLP](https://dblp.org): data released under CC0.
- [Semantic Scholar API](https://www.semanticscholar.org/product/api): subject to its terms; an API key is optional and raises the rate limit (`citecheck setup` can save one).
- [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/): free metadata API.
- [OpenAlex](https://openalex.org): CC0.

## Privacy

With a local index, nothing leaves your machine. When a paper is not found in the index (or no index is installed), only that entry's title and DOI are sent to Semantic Scholar, Crossref and OpenAlex. Your `.bib`, source files and API key are never uploaded. Use `--offline` to send nothing at all.

## Development

```bash
python -m unittest discover -s tests -v
```

Standard library only, Python 3.9+. MIT licensed; see [LICENSE](LICENSE).
