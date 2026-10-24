"""Canonical manifest, path, and report validation shared by CLI and preparation."""

import json
import io
import re
from datetime import datetime, timedelta
from pathlib import Path

from .errors import DataError
from .policy import startDate, endDate


def readJsonBytes(filePath, label):
  try:
    payload = filePath.read_bytes()
    with io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8") as stream:
      return payload, json.load(stream)
  except FileNotFoundError as error:
    raise DataError(f"{label} was not found: {filePath}") from error
  except json.JSONDecodeError as error:
    detail = f"line {error.lineno}, column {error.colno}"
    raise DataError(f"{label} is not valid JSON ({detail}): {filePath}") from error
  except UnicodeError as error:
    raise DataError(f"{label} is not valid UTF-8: {filePath}") from error
  except OSError as error:
    raise DataError(f"Could not read {label}: {filePath}") from error


def readJson(filePath, label):
  return readJsonBytes(filePath, label)[1]


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


def loadManifest(root):
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


def iterValidatedPages(root, pageReader=readJson):
  root = Path(root)
  manifest = loadManifest(root)
  reportIds = set()
  reportCount = 0

  for windowIndex, window in enumerate(manifest["windows"], 1):
    windowCount = 0
    for fileName in window["files"]:
      filePath = getPagePath(root, fileName)
      page = pageReader(filePath, "Data page")
      if not isinstance(page, dict) or not isinstance(page.get("results"), list):
        raise DataError(f"Data page results must be an array: {filePath}")
      for reportIndex, report in enumerate(page["results"], 1):
        reportId = checkReport(report, window["startDate"], window["endDate"],
                              f"Report {reportIndex} in {fileName}")
        if reportId in reportIds:
          raise DataError(f"Duplicate report_number found: {reportId}")
        reportIds.add(reportId)
      windowCount += len(page["results"])
      reportCount += len(page["results"])
      yield fileName, page["results"]
    if windowCount != window["savedCount"]:
      raise DataError(f"Loaded count does not match manifest window {windowIndex}.")

  if reportCount != manifest["savedCount"] or len(reportIds) != manifest["uniqueCount"]:
    raise DataError("Loaded report counts do not reconcile with the manifest.")


def loadReports(root):
  return [report for _, page in iterValidatedPages(root) for report in page]
