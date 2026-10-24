import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from fda_core import (
  startDate,
  endDate,
  startYear,
  endYear,
  maxAge,
  yearPattern,
  usage,
  ageFactors,
  termPunctuation,
  termSpaces,
  numericTerms,
  mixedFractions,
  numericSymbols,
  vitaminFamilyRule,
  productAliasRules,
  aliasGroups,
  termAliases,
  Part2Error,
  UsageError,
  DataError,
  parseArgs,
  getSuspectProducts,
  selectReports,
  ageInYears,
  getGender,
  normalizeTerm,
  normalizeTerms,
  getProductLabel,
  readJson,
  checkDate,
  checkCount,
  checkTerms,
  getPagePath,
  checkReport,
  iterValidatedPages,
)
from fda_core import loadManifest as _loadManifest, loadReports as _loadReports


dataDir = Path(__file__).resolve().parent / "data"
chartsDir = Path(__file__).resolve().parent / "charts"


def loadManifest(root=dataDir):
  return _loadManifest(root)


def loadReports(root=dataDir):
  return _loadReports(root)


def makeDataFrame(selected):
  columns = ["report", "suspectProducts", "outcomes", "reactions", "year", "ageYears", "gender"]
  rows = []
  for selectedReport in selected:
    report = selectedReport["report"]
    rows.append({
      "report": report,
      "suspectProducts": normalizeTerms(selectedReport["suspectProducts"], "product"),
      "outcomes": normalizeTerms(report["outcomes"], "outcome"),
      "reactions": normalizeTerms(report["reactions"], "reaction"),
      "year": int(report["date_started"][:4]),
      "ageYears": ageInYears(report["consumer"]),
      "gender": getGender(report["consumer"]),
    })
  return pd.DataFrame(rows, columns=columns)


def getValidAges(dataFrame, gender=None):
  ages = dataFrame["ageYears"] if gender is None else dataFrame.loc[
    dataFrame["gender"] == gender, "ageYears"]
  ages = ages.dropna().to_numpy(dtype=float)
  return ages[np.isfinite(ages)]


def getAgeAverage(dataFrame, gender=None):
  ages = getValidAges(dataFrame, gender)
  return float(np.mean(ages)) if ages.size else None


def getAgeAverages(dataFrame):
  return {
    "total": getAgeAverage(dataFrame),
    "female": getAgeAverage(dataFrame, "Female"),
    "male": getAgeAverage(dataFrame, "Male"),
  }


def formatAge(age):
  return "N/A" if age is None else f"{age:.2f} years"


def getTopTerms(dataFrame, column, limit=25):
  terms = dataFrame[column].explode().dropna()
  if terms.empty:
    return []
  counts = [(term, int(count)) for term, count in terms.value_counts().items()]
  return sorted(counts, key=lambda item: (-item[1], item[0]))[:limit]


def printTopTerms(title, terms, field=None):
  print(f"Top 25 {title}:")
  if not terms:
    print("  No matching values.")
    return
  for index, (term, count) in enumerate(terms, 1):
    label = getProductLabel(term) if field == "product" else term
    print(f"  {index}. {label}: {count:,}")


def printSummary(dataFrame):
  averages = getAgeAverages(dataFrame)
  print(f"Total Records: {len(dataFrame):,}")
  print()
  printTopTerms("Outcomes", getTopTerms(dataFrame, "outcomes"))
  print()
  printTopTerms("Reactions", getTopTerms(dataFrame, "reactions"))
  print()
  printTopTerms("Suspect Products", getTopTerms(dataFrame, "suspectProducts"), field="product")
  print()
  print("Average Consumer Age:")
  print(f"  Total Avg: {formatAge(averages['total'])}")
  print(f"  Female Avg: {formatAge(averages['female'])}")
  print(f"  Male Avg: {formatAge(averages['male'])}")


def getGroupedSummary(dataFrame, groupBy):
  if groupBy not in ("year", "gender", "product"):
    raise UsageError("--group-by requires year, gender, or product.")

  if groupBy == "product":
    # Lists already contain each canonical name once per report; only memberships expand.
    products = dataFrame["suspectProducts"].explode().dropna()
    groups = ((name, dataFrame.loc[products.index[products == name]])
              for name, _ in getTopTerms(dataFrame, "suspectProducts"))
  else:
    labels = dataFrame["gender"].fillna("Unknown") if groupBy == "gender" else dataFrame["year"]
    groups = dataFrame.groupby(labels, sort=True)

  summaries = []
  coveredRows = set()
  for label, group in groups:
    ages = getValidAges(group)
    summaries.append({
      "label": str(label),
      "recordCount": len(group),
      "validAgeCount": int(ages.size),
      "averageAge": float(np.mean(ages)) if ages.size else None,
      "outcomes": getTopTerms(group, "outcomes"),
      "reactions": getTopTerms(group, "reactions"),
    })
    coveredRows.update(group.index)

  return {
    "groupBy": groupBy,
    "groups": summaries,
    "totalRecords": len(dataFrame),
    "coveredRecords": len(coveredRows),
    "memberships": sum(group["recordCount"] for group in summaries),
  }


def printGroupedSummary(summary):
  groupBy = summary["groupBy"]
  print(f"Grouped Summary by {groupBy.title()}:")
  if groupBy == "product":
    print("Top 25 canonical suspect-product groups; reports can appear in multiple groups.")
    print(f"Coverage: {summary['coveredRecords']:,} of {summary['totalRecords']:,} distinct selected reports; "
          f"{summary['memberships']:,} displayed product memberships.")
  else:
    print("Only groups with matching reports are shown.")
    if groupBy == "gender":
      print("Unknown includes missing or unrecognized gender values.")
  if not summary["groups"]:
    print("  No matching groups.")
    return

  for group in summary["groups"]:
    label = getProductLabel(group["label"]) if groupBy == "product" else group["label"]
    if groupBy == "year" and label == str(endYear):
      label += " (January 1 only in this snapshot)"
    print(f"\n{label}:")
    print(f"  Records: {group['recordCount']:,}")
    print(f"  Reports with Valid Age: {group['validAgeCount']:,}")
    print(f"  Average Consumer Age: {formatAge(group['averageAge'])}")
    printTopTerms("Outcomes", group["outcomes"])
    printTopTerms("Reactions", group["reactions"])


def getYearCounts(dataFrame, filters):
  years = range(filters["startYear"], filters["endYear"] + 1)
  return dataFrame["year"].value_counts().reindex(years, fill_value=0).sort_index()


def buildChart(dataFrame, filters):
  import matplotlib
  matplotlib.use("Agg")
  from matplotlib import pyplot as plt
  from matplotlib.ticker import MaxNLocator

  yearCounts = getYearCounts(dataFrame, filters)
  ages = getValidAges(dataFrame)
  figure, (yearChart, ageChart) = plt.subplots(2, 1, figsize=(12, 10))

  yearChart.bar(yearCounts.index, yearCounts.values, color="#4C78A8")
  yearChart.set_title("Total Cases by Year")
  yearChart.set_xlabel("Year")
  yearChart.set_ylabel("Cases")
  yearChart.set_xticks(list(yearCounts.index))
  yearChart.tick_params(axis="x", rotation=45)
  yearChart.yaxis.set_major_locator(MaxNLocator(integer=True))
  yearChart.grid(axis="y", color="#D9D9D9", linewidth=0.8)
  yearChart.set_axisbelow(True)
  if not yearCounts.sum():
    yearChart.set_ylim(0, 1)

  # Integer edges make every bar one age year wide and keep an exact age of 125.
  ageChart.hist(ages, bins=np.arange(0, maxAge + 2), color="#4C78A8",
                edgecolor="white", linewidth=0.25)
  ageChart.set_title("Consumer Age Distribution (One-Year Bins)")
  ageChart.set_xlabel("Age in Years")
  ageChart.set_ylabel("Reports with Valid Age")
  ageChart.set_xlim(0, maxAge + 1)
  ageChart.set_xticks([*range(0, 121, 10), maxAge])
  ageChart.yaxis.set_major_locator(MaxNLocator(integer=True))
  ageChart.grid(axis="y", color="#D9D9D9", linewidth=0.8)
  ageChart.set_axisbelow(True)
  if not ages.size:
    ageChart.set_ylim(0, 1)
    ageChart.text(0.5, 0.5, "No valid ages", ha="center", va="center",
                  transform=ageChart.transAxes)

  scope = f"{filters['startYear']}-{filters['endYear']}"
  if filters["endYear"] == endYear:
    scope += " (data through January 1, 2026)"
  if filters["productFilter"]:
    scope += f"\nSuspect product contains: {' '.join(filters['productFilter'].split())}"
  figure.suptitle(f"FDA Food Adverse Event Reports\n{scope}", fontsize=14,
                  wrap=True, parse_math=False)
  figure.tight_layout(rect=(0, 0, 1, 0.93))
  return figure


def saveChart(dataFrame, filters, root=chartsDir, executionTime=None):
  executionTime = datetime.now() if executionTime is None else executionTime
  root = Path(root)
  chartPath = root / executionTime.strftime("%Y%m%d-%H%M%S-%f.png")
  try:
    root.mkdir(parents=True, exist_ok=True)
  except OSError as error:
    raise DataError(f"Could not create the charts directory: {root}") from error

  figure = buildChart(dataFrame, filters)
  from matplotlib import pyplot as plt
  try:
    figure.savefig(chartPath, dpi=150, bbox_inches="tight", facecolor="white")
  except OSError as error:
    raise DataError(f"Could not save chart: {chartPath}") from error
  finally:
    plt.close(figure)
  return chartPath


def main(values=None, root=dataDir, chartRoot=chartsDir, executionTime=None):
  executionTime = datetime.now() if executionTime is None else executionTime
  filters = parseArgs(sys.argv[1:] if values is None else values)
  reports = loadReports(root)
  selected = selectReports(reports, filters)
  dataFrame = makeDataFrame(selected)
  grouped = getGroupedSummary(dataFrame, filters["groupBy"]) if filters["groupBy"] else None
  chartPath = saveChart(dataFrame, filters, chartRoot, executionTime)
  printSummary(dataFrame)
  if grouped is not None:
    print()
    printGroupedSummary(grouped)
  print()
  print(f"Chart saved to: {chartPath}")
  return dataFrame


def runCli():
  try:
    main()
    return 0
  except UsageError as error:
    print(f"Error: {error}", file=sys.stderr)
    print(usage, file=sys.stderr)
    return 2
  except DataError as error:
    print(f"Error: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  sys.exit(runCli())
