# FDA Food Safety Data Pipeline

Downloads FDA food adverse-event reports for January 1, 2002 through January 1,
2026, then filters, summarizes, and visualizes the downloaded data.

Data comes from the [openFDA Food Event API](https://open.fda.gov/apis/food/event/)
using its [cursor-based paging](https://open.fda.gov/apis/paging/).

The repository includes the snapshot downloaded on September 4, 2026: **97,652
reports across 987 JSON page files**, plus its completion manifest. Date filtering
uses `date_started`, the reported event start date. The final 2026 interval contains
January 1 only.

## Requirements

- Node.js 18 or newer. Part 1 uses Node's built-in `fetch` and has no package
  dependencies.
- Python 3.9 or newer.

The current validation environment uses Node.js 26.5.0 and Python 3.14.6, with
pandas 3.0.5, numpy 2.5.2, and matplotlib 3.11.1. Older supported versions have
not been separately tested in this revision.

Install the required Python libraries in a virtual environment:

```bash
python -m venv .venv
```

Activate it with `.\.venv\Scripts\Activate.ps1` in Windows PowerShell or
`source .venv/bin/activate` on macOS/Linux, then install the dependencies:

```bash
python -m pip install -r requirements.txt
```

If it is not activated, replace `python` below with `.venv/bin/python` on
macOS/Linux or `.\.venv\Scripts\python.exe` on Windows.

## Part 1: Download the FDA data

```bash
node part1.js
```

The downloader:

- sends `limit=100` on every page request, including retries;
- divides the fixed range into 24 disjoint date windows;
- processes three windows concurrently by default while following each window's FDA
  cursor sequentially;
- spaces request starts by at least 300 ms, applies 30-second timeouts, and retries temporary
  network failures and HTTP 408, 429, 500, 502, 503, and 504 responses;
- validates dates, report identifiers, cursor links, and reported totals; and
- publishes `data/manifest.json` as complete only after all files and counts reconcile.

Each request has at most four attempts. Retries obey the same pacing gate and use
exponential backoff, jitter, and `Retry-After`; HTTP 429 also delays other workers.
Requests within a window remain sequential, while different windows can have requests
in flight together. Paging continues until the next-page link disappears, even if a
page is short or reaches the upper date. FDA can supply one extra cursor returning
an exact no-match response; that is accepted only when the downloaded counts reconcile.

`CONCURRENCY_LIMIT` accepts any positive integer. `OPENFDA_API_KEY` is optional:

```powershell
$env:CONCURRENCY_LIMIT = "3"
$env:OPENFDA_API_KEY = "your-openfda-api-key"
node part1.js
```

The script deliberately refuses to overwrite an existing `data` directory. Because
this repository includes the completed download, move that directory somewhere safe
before testing a fresh ingestion. Interrupted and failed runs retain an incomplete
manifest and are not treated as analysis-ready; resume support is not implemented.
After a worker fails, no new windows are assigned, while already active windows finish
before the script exits with an error. Stable reported totals and unique report IDs
are checked throughout a run. The live API can change, so these checks do not establish
a transactionally frozen API snapshot or guarantee identical results on a later download.

## Part 2: Analyze and visualize

Run the overall analysis:

```bash
python part2.py
```

Arguments may appear in any order:

```bash
python part2.py 2022
python part2.py 2022 2023
python part2.py CARROTS
python part2.py 2024 2026 CARROTS
python part2.py CARROTS 2026 2024
python part2.py --product "2000"
python part2.py 2024 2026 --product "BABY CARROTS"
```

- No year analyzes 2002 through the end of the downloaded snapshot.
- One year analyzes that year through the snapshot end.
- Two years select an inclusive range; their argument order does not matter.
- Positional product words form one case-insensitive substring filter. A report matches
  only when the text occurs in a product whose FDA role is `SUSPECT`.
- In a filtered product ranking, only matching suspect products are counted.
- `--product TEXT` explicitly supplies that same filter, including numeric names.
  Quote multiword values. Use either positional product text or `--product`; missing
  values, repeated options, and mixing both filter forms produce an error.

Here, “Present” means the fixed Part 1 snapshot ending January 1, 2026. Exact
four-digit positional arguments are interpreted as years. For example, `2022` selects
a starting year, while `--product "2022"` searches raw product names for that text.
Filtering happens before consolidation: `JIFF` can display a canonical JIF label, but
it does not select reports whose raw names contain only `JIF`. A selected report retains
all its outcomes and reactions, including those reported alongside other products.

The command prints:

1. total matching records;
2. up to 25 outcomes;
3. up to 25 reactions;
4. up to 25 suspect products; and
5. total, female, and male average consumer ages.

The saved snapshot contains 13 distinct outcome labels, so its overall outcome list
has 13 entries. The Top 25 lists show only categories present in the selected reports.

### Consolidation and counting

Reports are counted by unique `report_number`. Formatting cleanup and reviewed,
field-specific aliases apply to product names, reactions, and outcomes. Canonical
terms are deduplicated within each report before counting, so equivalent descriptions
in one report contribute once. A report can still contribute to multiple distinct terms.

Normalization handles Unicode, case, spaces, apostrophes, and ordinary text punctuation.
Numeric separators are preserved: `2.5`, `2-5`, and `2/5` remain different. Mixed fractions
and Unicode numeric symbols are handled before cleanup to avoid changing their values.
The alias tables in `part2.py` document the exact reviewed equivalents, including:

- `Vitamin D`, `Vitamin D3`, and inspected D3 supplement descriptions in the assignment's
  Vitamin D reporting family, while retaining explicit strengths and formulations;
- reordered or misspelled JIF creamy peanut butter names, with crunchy and natural
  formulations kept separate;
- `HALLUCINATIONS` and `HALLUCINATION`; and
- `HOSPITALISATION` and `HOSPITALIZATION` (a supported spelling variant absent from
  the current snapshot).

The policy uses a finite reviewed mapping. Unreviewed descriptions remain separate;
it does not infer that every similar string names the same physical product or clinical
condition. Brands, doses, flavors, preparations, formula versions, stages, and anatomical
qualifiers are retained unless a specific equivalence has been reviewed. Adding an alias
requires representative examples and nearby examples that must stay distinct.

`EXEMPTION 4` is displayed as an undisclosed product-name bucket that may include
different products. Its canonical key and counts are retained. FDA documents these
product-name redactions in [its data notes, section E](https://www.fda.gov/media/97035/download?attachment=).
Use the raw text `EXEMPTION 4` when filtering; the explanatory display label is not a
search alias.

### Consumer ages

Ages expressed in years, months, weeks, days, or decades are converted to years.
Months use 1/12 of a year, weeks use 7/365.25, days use 1/365.25, and decades use
10 years. Missing, malformed, overflowing, non-finite, negative, unsupported, and
converted ages above 125 are excluded from age calculations. The inclusive 0–125-year
range is an explicit data-hygiene policy; zero-age infants remain valid. Every mean
uses only its group's valid-age count. Excluding an age does not exclude its report
from record or term counts. Unknown-gender records with valid ages contribute to
the total average.

### Optional grouped comparisons

```bash
python part2.py --group-by year
python part2.py --group-by gender
python part2.py 2024 2026 JIF --group-by product
```

Filters run first. Each group shows report count, valid-age count, average age, and
Top 25 outcomes and reactions. Year groups are chronological; gender groups retain
missing or unrecognized values as `Unknown`. Only groups with matching reports are
printed, and the 2026 group is marked as January 1 only. No valid ages means `N/A`.

Product grouping shows the Top 25 canonical suspect-product groups, with descending
report counts and alphabetical ties. A report can belong to several product groups.
The output distinguishes the sum of product memberships from the number of distinct
reports covered, and the displayed groups may cover only part of the selection.
Product-group counts cannot be added to recover the overall count, and overlapping
group averages cannot be combined as though their groups were disjoint. Grouping
does not expand the overall report-level table or change the overall chart.

### Charts and error behavior

Every successful run also writes `charts/<execution-timestamp>.png` with two subplots:

- total cases for every year in the selected range, including zero-count years; and
- valid consumer ages in one-year-wide histogram bins.

Fractional ages fall into `[year, year + 1)` bins; an exact age of 125 is retained.
Empty selections still produce a chart with zero annual counts and no valid ages.
Output directories are beside the scripts, regardless of the working directory.
Each successful run creates another timestamped chart. This revision retains one
fresh overall-run chart as the deliverable.

The analyzer refuses incomplete manifests, missing or malformed listed files,
non-contiguous date coverage, count mismatches, out-of-window records, and duplicate
report identifiers instead of presenting a partial download as complete.
Invalid command arguments exit with status 2; data or output failures exit with status 1.

## Validation and interpretation

The local validation includes 99 passing Node checks (including the independent scan
of all 987 saved pages) and 66 passing Python tests. The live FDA check is opt-in and
was skipped during this final revision. Tests and detailed audit notes are kept local.
`python -m pip check` and `node --check part1.js` also pass in the validated environment.

The final no-argument run takes **25.8 seconds** on the validation machine, including
interpreter startup, loading, validation, output formatting, and PNG serialization.
Its outputs match independent per-report term counts, age conversions, averages,
annual counts, and one-year age-bin counts. The optional product mode was also measured
below one minute with the same code and snapshot. Timing uses captured stdout; other
machines, terminal rendering, and future dataset sizes may differ.

Completeness is reconciled against the totals reported during the saved download.
This revision does not requery FDA to claim that the older snapshot matches today's
API contents. Term consolidation remains a documented, conservative policy rather
than exhaustive semantic matching.

These are counts of reports mentioning terms. They do not establish unique people,
incidence, relative risk, or causation. When a report names multiple products and
reactions, the data do not identify which product caused a reaction. See
[FDA's interpretation guidance](https://open.fda.gov/apis/food/event/).

## Repository layout

- `part1.js` — concurrent, cursor-based FDA downloader.
- `part2.py` — command-line analysis and visualization tool.
- `requirements.txt` — Python dependency ranges.
- `data/manifest.json` — completion record and page inventory.
- `data/<date-window>/` — original FDA response pages.
- `charts/` — timestamped two-panel analysis output.

![Overall FDA adverse-event analysis](charts/20261007-223426-542960.png)
