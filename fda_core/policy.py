"""Canonical FDA selection, normalization, and age rules; standard library only."""

import math
import re
import unicodedata

from .errors import UsageError


startDate = "20020101"
endDate = "20260101"
startYear = 2002
endYear = 2026
maxAge = 125
yearPattern = re.compile(r"^\d{4}$")
usage = ("Usage: python part2.py [YEAR or PRODUCT] ... [--product TEXT] "
         "[--group-by year|gender|product]")
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
# Exact reviewed names preserve unlisted strengths, formulations and clinical qualifiers.
aliasGroups = {
  "product": {
    "JIF CREAMY PEANUT BUTTER": [
      "JIF PEANUT BUTTER CREAMY", "JIFF CREAMY PEANUT BUTTER",
      "JIFF CREAMY PEANUTBUTTER", "JIF CREAMY PEANUTBUTTER",
    ],
    "PETER PAN CREAMY PEANUT BUTTER": ["PETER PAN PEANUT BUTTER CREAMY", "PETER PAN CEAMY PEANUT BUTTER"],
    "PRESERVISION AREDS 2": [
      "AREDS 2 PRESERVISION", "PRESERVISION AREDS2", "PRESERVISION ARDS 2", "PRESERVISION AREDS-2",
    ],
    "RAW OYSTERS": ["OYSTERS RAW"],
    "CENTRUM SILVER WOMENS 50 PLUS MULTIMINERALS MULTIVITAMINS TABLET": [
      "CENTRUM SILVER WOMENS 50 PLUS MULTIVITAMINS MULTIMINERALS TABLET",
      "CENTRUM SILVER WOMENS 50 PLUS MULTIMINERAL MULTIVITAMINS TABLET",
      "CENTRUM SILVER WOMENS 50 PLUS MUL TIMINERALS MULTIVITAMINS TABLET",
      "CENTRUM SILVER WOMENS 50 PLUS MULTIMINERALS MULTIVITAMIN TABLET",
    ],
    "DAILY HARVEST FRENCH LENTIL AND LEEK CRUMBLES": [
      "DAILY HARVEST FRENCH LEEK AND LENTIL CRUMBLES", "DAILY HARVEST CRUMBLES FRENCH LENTIL AND LEEK",
      "DAILY HARVEST FRENCH LENTIL AND LEEK CRUMBLE", "DAILY HARVEST FRENCH LENTILS AND LEEK CRUMBLES",
      "DAILY HARVEST FRENCH LENTIL PLUS LEEK CRUMBLES", "DAILY HARVEST CRUMBLES FRENCH LENTIL PLUS LEEK",
      "DAILY HARVEST FRENCH LEEK PLUS LENTIL CRUMBLES", "DAILY HARVEST FRENCH LENTILES AND LEEK CRUMBLES",
      "DAILY HARVEST FRENCH LENTIL AND LEEKS CRUMBLES",
    ],
    "CENTRUM SILVER ULTRA WOMENS MULTIMINERALS MULTIVITAMINS TABLET": [
      "CENTRUM SILVER ULTRA WOMENS MULTIVITAMINS MULTIMINERALS TABLET",
      "CENTRUM SILVER ULTRA WOMENS MULTIMINERAL MULTIVITAMINS TABLET",
      "CENTRUM SILVER ULTRA WOMENS MULTIMINERAL MULTIVITAMIN TABLET",
      "CENTRUM SILVER ULTRA WOMENS MULTIMINERALS MULTIVITAMINS TABLETS",
    ],
    "JIF EXTRA CRUNCHY PEANUT BUTTER": [
      "JIF PEANUT BUTTER EXTRA CRUNCHY", "JIF EXTRA CRUNCHY PEANUT BETTER", "JIF EXTRA CRUNCHY PEAUNUT BUTTER",
    ],
    "JIF NATURAL CREAMY PEANUT BUTTER": ["JIF NATURAL PEANUT BUTTER CREAMY", "NATURAL JIF CREAMY PEANUT BUTTER"],
    "GLUTEN FREE CHEERIOS": ["CHEERIOS GLUTEN FREE"],
    "ONE A DAY WOMENS": ["WOMENS ONE A DAY"],
    "GREAT VALUE CREAMY PEANUT BUTTER": ["GREAT VALUE PEANUT BUTTER CREAMY"],
    "WEN SWEET ALMOND MINT CLEANSING CONDITIONER": ["WEN CLEANSING CONDITIONER SWEET ALMOND MINT"],
    "ARBONNE ESSENTIALS PROTEIN SHAKE MIX CHOCOLATE": ["ARBONNE ESSENTIALS CHOCOLATE PROTEIN SHAKE MIX"],
    "ARBONNE ESSENTIALS PROTEIN SHAKE MIX VANILLA": ["ARBONNE ESSENTIALS VANILLA PROTEIN SHAKE MIX"],
    "HERBALIFE MULTIVITAMIN COMPLEX FORMULA 2": ["HERBALIFE FORMULA 2 MULTIVITAMIN COMPLEX"],
    "PETER PAN CRUNCHY PEANUT BUTTER": ["PETER PAN PEANUT BUTTER CRUNCHY"],
    "SABRA CLASSIC HUMMUS": ["SABRA HUMMUS CLASSIC"],
    "SUPER BETA PROSTATE": ["SUPER BETA PROSATATE"],
    "WEN CLEANSING CONDITIONER": [
      "WEN CLEANSING CONDITIONERS", "WEN CLEANING CONDITIONER", "WEN CLENSING CONDITIONER", "WEN CLEASING CONDITIONER",
    ],
    "HYDROXYCUT REGULAR RAPID RELEASE CAPLETS": [
      "HYDROXYCUT REGULAR RAPID RELASE CAPLETS", "HYDROXCUT REGULAR RAPID RELEASE CAPLETS",
      "HYDOXYCUT REGULAR RAPID RELEASE CAPLETS",
    ],
    "EOS LIP BALM": ["EOS LIPBALM"],
    # These supplement names join the assignment's Vitamin D reporting family.
    "VITAMIN D": ["D3"],
    "NATURE MADE VITAMIN D": ["NATURE MADE D3"],
    "NATURES BOUNTY VITAMIN D": ["NATURES BOUNTY D3"],
    "NATURE MADE VITAMIN D 5000 IU": ["NATURE MADE D3 5000 IU"],
    "NATURE MADE VITAMIN D 2000 IU": [
      "NATURE MADE VITAMIN D 2000IU", "NATURE MADE D3 2000 IU", "NATURE MADE D3 2000IU",
    ],
  },
  "reaction": {
    "HALLUCINATION": ["HALLUCINATIONS"],
    "AUTOIMMUNE HAEMOLYTIC ANAEMIA": ["ANAEMIA HAEMOLYTIC AUTOIMMUNE"],
    "DYSBACTERIOSIS": ["DISBACTERIOSIS"],
  },
  "outcome": {
    "HOSPITALIZATION": ["HOSPITALISATION"],
  },
}
# Build the reverse lookup once; each normalized term then needs one exact lookup.
termAliases = {
  field: {alias: canonical for canonical, aliases in groups.items() for alias in aliases}
  for field, groups in aliasGroups.items()
}


def parseArgs(values):
  years = []
  productWords = []
  explicitProduct = None
  groupBy = None
  index = 0

  # Exact four-digit arguments are years; the remaining words form one product filter.
  while index < len(values):
    value = values[index].strip()
    index += 1
    if not value:
      raise UsageError("Arguments cannot be blank.")
    if value == "--product":
      if explicitProduct is not None:
        raise UsageError("Provide --product only once.")
      if index == len(values) or values[index].strip().startswith("--"):
        raise UsageError("--product requires a text value; quote names containing spaces.")
      # Consume numeric product names before the positional year check.
      explicitProduct = values[index].strip()
      index += 1
      if not explicitProduct:
        raise UsageError("--product cannot be blank.")
    elif value == "--group-by":
      if groupBy is not None:
        raise UsageError("Provide --group-by only once.")
      if index == len(values):
        raise UsageError("--group-by requires year, gender, or product.")
      groupBy = values[index].strip()
      index += 1
      if groupBy not in ("year", "gender", "product"):
        raise UsageError("--group-by requires year, gender, or product.")
    elif value.startswith("--"):
      raise UsageError(f"Unknown option: {value}")
    elif yearPattern.fullmatch(value):
      years.append(int(value))
    else:
      productWords.append(value)

  if len(years) > 2:
    raise UsageError("Provide no more than two years.")
  if any(year < startYear or year > endYear for year in years):
    raise UsageError(f"Years must be between {startYear} and {endYear}.")
  if explicitProduct is not None and productWords:
    raise UsageError("Use either positional product text or --product, not both.")

  years.sort()
  selectedStart = years[0] if years else startYear
  selectedEnd = years[1] if len(years) == 2 else endYear
  productFilter = explicitProduct if explicitProduct is not None else " ".join(productWords) or None
  return {"startYear": selectedStart, "endYear": selectedEnd,
          "productFilter": productFilter, "groupBy": groupBy}


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
  except (TypeError, ValueError, OverflowError):
    return None

  factor = ageFactors.get(unit.strip().casefold())
  if factor is None or not math.isfinite(age):
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
  return termAliases.get(field, {}).get(value, value)


def normalizeTerms(values, field):
  # Dedupe after aliases so equivalent values in one report contribute only once.
  normalized = {normalizeTerm(value, field) for value in values}
  return sorted(value for value in normalized if value)


def getProductLabel(name):
  # This disclosure bucket can contain multiple unidentified products.
  if name == "EXEMPTION 4":
    return "Undisclosed product names (EXEMPTION 4; may include multiple products)"
  return name
