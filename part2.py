import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator


matplotlib.use("Agg")
from matplotlib import pyplot as plt


startDate = "20020101"
endDate = "20260101"
startYear = 2002
endYear = 2026
maxAge = 125
dataDir = Path(__file__).resolve().parent / "data"
chartsDir = Path(__file__).resolve().parent / "charts"
yearPattern = re.compile(r"^\d{4}$")
usage = "Usage: python part2.py [YEAR or PRODUCT] ..."
ageFactors = {
  "year(s)": 1,
  "month(s)": 1 / 12,
  "week(s)": 7 / 365.25,
  "day(s)": 1 / 365.25,
  "decade(s)": 10,
}
termPunctuation = re.compile(r"[^\w%]+", re.UNICODE)
termSpaces = re.compile(r"\s+")
numericTerms = re.compile(r"([+-]?(?:\d+(?:\s*[.,/-]\s*\d+)*|\.\d+))")
mixedFractions = re.compile(r"(?<=\d)([\u00bc-\u00be\u2150-\u215e])")
numericSymbols = str.maketrans({"–": "-", "—": "-", "−": "-", "⁄": "/", "∕": "/"})
vitaminFamilyRule = re.compile(
  r"\b(?:VIT|VITAMIN)[\s-]+D-?[23]"
  r"(?![\w]|[.,/-]\s*\d|\s*(?:%|(?:MCG|UG|ΜG|MG|G|IU)\b))"
)
# These aliases cover inspected formatting families without guessing at fuzzy similarity.
productAliasRules = [
  (re.compile(r"\bVIT\s+D\b"), "VITAMIN D"),
  (re.compile(r"\bNATUREMADE\b"), "NATURE MADE"),
  (re.compile(r"\bSOFT\s*GELS?\b"), "SOFT GEL"),
]


class Part2Error(Exception):
  pass


class UsageError(Part2Error):
  pass


class DataError(Part2Error):
  pass


def parseArgs(values):
  years = []
  productWords = []

  # Exact four-digit arguments are years; the remaining words form one product filter.
  for value in values:
    value = value.strip()
    if not value:
      raise UsageError("Arguments cannot be blank.")
    if yearPattern.fullmatch(value):
      years.append(int(value))
    else:
      productWords.append(value)

  if len(years) > 2:
    raise UsageError("Provide no more than two years.")
  if any(year < startYear or year > endYear for year in years):
    raise UsageError(f"Years must be between {startYear} and {endYear}.")

  years.sort()
  selectedStart = years[0] if years else startYear
  selectedEnd = years[1] if len(years) == 2 else endYear
  productFilter = " ".join(productWords) or None
  return {"startYear": selectedStart, "endYear": selectedEnd, "productFilter": productFilter}


def readJson(filePath, label):
  try:
    with filePath.open("r", encoding="utf-8") as file:
      return json.load(file)
  except FileNotFoundError as error:
    raise DataError(f"{label} was not found: {filePath}") from error
  except json.JSONDecodeError as error:
    detail = f"line {error.lineno}, column {error.colno}"
    raise DataError(f"{label} is not valid JSON ({detail}): {filePath}") from error
  except UnicodeError as error:
    raise DataError(f"{label} is not valid UTF-8: {filePath}") from error
  except OSError as error:
    raise DataError(f"Could not read {label}: {filePath}") from error


def checkDate(value, label):
  if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
    raise DataError(f"{label} must be an eight-digit date.")
  try:
    datetime.strptime(value, "%Y%m%d")
  except ValueError as error:
    raise DataError(f"{label} is not a valid calendar date: {value}") from error
  return value


def checkCount(value, label):
  if type(value) is not int or value < 0:
    raise DataError(f"{label} must be a nonnegative integer.")
  return value


def checkTerms(values, label):
  if not isinstance(values, list):
    raise DataError(f"{label} must be an array.")
  for index, value in enumerate(values, 1):
    if not isinstance(value, str) or not value.strip():
      raise DataError(f"{label} item {index} must be a nonempty string.")
  return values


def getPagePath(root, fileName):
  if not isinstance(fileName, str) or not fileName:
    raise DataError("Every manifest file path must be a nonempty string.")

  root = root.resolve()
  filePath = (root / fileName).resolve()
  if filePath == root or not filePath.is_relative_to(root):
    raise DataError(f"Manifest file path leaves the data directory: {fileName}")
  return filePath


def loadManifest(root=dataDir):
  root = Path(root)
  manifest = readJson(root / "manifest.json", "Completion manifest")
  if not isinstance(manifest, dict):
    raise DataError("Completion manifest must contain a JSON object.")
  if type(manifest.get("schemaVersion")) is not int or manifest["schemaVersion"] != 1:
    raise DataError("Completion manifest schemaVersion must be 1.")
  if manifest.get("complete") is not True:
    raise DataError("Completion manifest does not mark the download complete.")
  if manifest.get("startDate") != startDate or manifest.get("endDate") != endDate:
    raise DataError(f"Completion manifest must cover {startDate} through {endDate}.")
  if manifest.get("pageLimit") != 100:
    raise DataError("Completion manifest pageLimit must be 100.")

  windows = manifest.get("windows")
  if not isinstance(windows, list) or not windows:
    raise DataError("Completion manifest must contain at least one window.")
  if checkCount(manifest.get("windowCount"), "manifest.windowCount") != len(windows):
    raise DataError("Manifest windowCount does not match its windows.")

  listedPaths = set()
  savedCount = 0
  pageCount = 0
  expectedStart = startDate
  lastEnd = None
  for index, window in enumerate(windows, 1):
    if not isinstance(window, dict):
      raise DataError(f"Manifest window {index} must be a JSON object.")

    windowStart = checkDate(window.get("startDate"), f"window {index} startDate")
    windowEnd = checkDate(window.get("endDate"), f"window {index} endDate")
    if windowStart > windowEnd or windowStart < startDate or windowEnd > endDate:
      raise DataError(f"Manifest window {index} is outside the assignment bounds.")
    if windowStart != expectedStart:
      raise DataError("Manifest windows must be ordered, contiguous, and cover the assignment range.")
    expectedStart = (datetime.strptime(windowEnd, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    lastEnd = windowEnd

    windowSaved = checkCount(window.get("savedCount"), f"window {index} savedCount")
    windowTotal = checkCount(window.get("total"), f"window {index} total")
    files = window.get("files")
    if not isinstance(files, list):
      raise DataError(f"Manifest window {index} files must be an array.")
    if checkCount(window.get("pageCount"), f"window {index} pageCount") != len(files):
      raise DataError(f"Manifest window {index} pageCount does not match its files.")
    if windowSaved != windowTotal:
      raise DataError(f"Manifest window {index} savedCount does not match its total.")

    for fileName in files:
      filePath = getPagePath(root, fileName)
      if filePath in listedPaths:
        raise DataError(f"Manifest lists a page more than once: {fileName}")
      listedPaths.add(filePath)

    savedCount += windowSaved
    pageCount += len(files)

  if lastEnd != endDate:
    raise DataError("Manifest windows must be ordered, contiguous, and cover the assignment range.")

  manifestSaved = checkCount(manifest.get("savedCount"), "manifest.savedCount")
  manifestTotal = checkCount(manifest.get("total"), "manifest.total")
  manifestUnique = checkCount(manifest.get("uniqueCount"), "manifest.uniqueCount")
  manifestPages = checkCount(manifest.get("pageCount"), "manifest.pageCount")
  if savedCount != manifestSaved or manifestSaved != manifestTotal or manifestSaved != manifestUnique:
    raise DataError("Manifest report counts do not reconcile.")
  if pageCount != manifestPages:
    raise DataError("Manifest pageCount does not reconcile with its windows.")
  return manifest


def checkReport(report, windowStart, windowEnd, label):
  if not isinstance(report, dict):
    raise DataError(f"{label} must be a JSON object.")

  reportId = report.get("report_number")
  if not isinstance(reportId, str) or not reportId.strip():
    raise DataError(f"{label} has no valid report_number.")

  reportDate = checkDate(report.get("date_started"), f"{label} date_started")
  if reportDate < windowStart or reportDate > windowEnd:
    raise DataError(f"{label} date_started is outside its manifest window.")

  products = report.get("products")
  if not isinstance(products, list):
    raise DataError(f"{label} products must be an array.")
  for index, product in enumerate(products, 1):
    if not isinstance(product, dict):
      raise DataError(f"{label} product {index} must be a JSON object.")
    if not isinstance(product.get("role"), str) or not product["role"].strip():
      raise DataError(f"{label} product {index} has no valid role.")
    if not isinstance(product.get("name_brand"), str) or not product["name_brand"].strip():
      raise DataError(f"{label} product {index} has no valid name_brand.")
  checkTerms(report.get("outcomes"), f"{label} outcomes")
  checkTerms(report.get("reactions"), f"{label} reactions")
  if not isinstance(report.get("consumer"), dict):
    raise DataError(f"{label} consumer must be a JSON object.")
  return reportId


def loadReports(root=dataDir):
  root = Path(root)
  manifest = loadManifest(root)
  reports = []
  reportIds = set()

  # Read only manifest-listed pages, but validate every record before applying filters.
  for windowIndex, window in enumerate(manifest["windows"], 1):
    windowCount = 0
    for fileName in window["files"]:
      filePath = getPagePath(root, fileName)
      page = readJson(filePath, "Data page")
      if not isinstance(page, dict) or not isinstance(page.get("results"), list):
        raise DataError(f"Data page results must be an array: {filePath}")

      for reportIndex, report in enumerate(page["results"], 1):
        label = f"Report {reportIndex} in {fileName}"
        reportId = checkReport(report, window["startDate"], window["endDate"], label)
        if reportId in reportIds:
          raise DataError(f"Duplicate report_number found: {reportId}")
        reportIds.add(reportId)
        reports.append(report)
      windowCount += len(page["results"])

    if windowCount != window["savedCount"]:
      raise DataError(f"Loaded count does not match manifest window {windowIndex}.")

  if len(reports) != manifest["savedCount"] or len(reportIds) != manifest["uniqueCount"]:
    raise DataError("Loaded report counts do not reconcile with the manifest.")
  return reports


def getSuspectProducts(report, productFilter=None):
  products = [product["name_brand"] for product in report["products"]
              if product["role"] == "SUSPECT"]
  if productFilter is None:
    return products
  searchText = productFilter.casefold()
  return [product for product in products if searchText in product.casefold()]


def selectReports(reports, filters):
  selected = []
  for report in reports:
    reportYear = int(report["date_started"][:4])
    if reportYear < filters["startYear"] or reportYear > filters["endYear"]:
      continue

    # Keep only matching suspect names for the later filtered product ranking.
    suspectProducts = getSuspectProducts(report, filters["productFilter"])
    if filters["productFilter"] is None or suspectProducts:
      selected.append({"report": report, "suspectProducts": suspectProducts})
  return selected


def ageInYears(consumer):
  if not isinstance(consumer, dict):
    return None

  age = consumer.get("age")
  unit = consumer.get("age_unit")
  if age is None or isinstance(age, bool) or not isinstance(unit, str):
    return None

  try:
    age = float(age)
  except (TypeError, ValueError):
    return None

  factor = ageFactors.get(unit.strip().casefold())
  if factor is None or not np.isfinite(age):
    return None

  convertedAge = age * factor
  return convertedAge if 0 <= convertedAge <= maxAge else None


def getGender(consumer):
  gender = consumer.get("gender")
  if not isinstance(gender, str):
    return None
  gender = gender.strip().casefold()
  return "Female" if gender == "female" else "Male" if gender == "male" else None


def normalizeTerm(value, field):
  # Separate a mixed fraction before NFKC turns 1½ into the misleading 11⁄2.
  value = mixedFractions.sub(r" \1", value)
  value = unicodedata.normalize("NFKC", value).upper().translate(numericSymbols)

  # Captured numeric spans keep their separators; only surrounding text is cleaned.
  parts = numericTerms.split(value)
  for index, part in enumerate(parts):
    if index % 2:
      parts[index] = termSpaces.sub("", part)
      continue
    part = part.replace("&", " AND ").replace("+", " PLUS ")
    for apostrophe in ("'", "’", "‘", "`", "ʼ", "\u0092"):
      part = part.replace(apostrophe, "")
    parts[index] = termPunctuation.sub(" ", part.replace("_", " "))
  value = termSpaces.sub(" ", "".join(parts)).strip()

  if field == "product":
    value = vitaminFamilyRule.sub("VITAMIN D", value)
    for pattern, replacement in productAliasRules:
      value = pattern.sub(replacement, value)
    value = termSpaces.sub(" ", value).strip()
  return value


def normalizeTerms(values, field):
  # Dedupe after aliases so equivalent values in one report contribute only once.
  normalized = {normalizeTerm(value, field) for value in values}
  return sorted(value for value in normalized if value)


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


def printTopTerms(title, terms):
  print(f"Top 25 {title}:")
  if not terms:
    print("  No matching values.")
    return
  for index, (term, count) in enumerate(terms, 1):
    print(f"  {index}. {term}: {count:,}")


def printSummary(dataFrame):
  averages = getAgeAverages(dataFrame)
  print(f"Total Records: {len(dataFrame):,}")
  print()
  printTopTerms("Outcomes", getTopTerms(dataFrame, "outcomes"))
  print()
  printTopTerms("Reactions", getTopTerms(dataFrame, "reactions"))
  print()
  printTopTerms("Suspect Products", getTopTerms(dataFrame, "suspectProducts"))
  print()
  print("Average Consumer Age:")
  print(f"  Total Avg: {formatAge(averages['total'])}")
  print(f"  Female Avg: {formatAge(averages['female'])}")
  print(f"  Male Avg: {formatAge(averages['male'])}")


def getYearCounts(dataFrame, filters):
  years = range(filters["startYear"], filters["endYear"] + 1)
  return dataFrame["year"].value_counts().reindex(years, fill_value=0).sort_index()


def buildChart(dataFrame, filters):
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
  chartPath = saveChart(dataFrame, filters, chartRoot, executionTime)
  printSummary(dataFrame)
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
