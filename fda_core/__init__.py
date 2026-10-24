"""Installable canonical FDA analysis policy and validation core."""

__version__ = "0.1.0"

from .errors import Part2Error, UsageError, DataError
from .policy import (
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
  parseArgs,
  getSuspectProducts,
  selectReports,
  ageInYears,
  getGender,
  normalizeTerm,
  normalizeTerms,
  getProductLabel,
)
from .validation import (
  readJsonBytes,
  readJson,
  checkDate,
  checkCount,
  checkTerms,
  getPagePath,
  loadManifest,
  checkReport,
  loadReports,
  iterValidatedPages,
)

__all__ = ["startDate","endDate","startYear","endYear","maxAge","yearPattern","usage","ageFactors","termPunctuation","termSpaces","numericTerms","mixedFractions","numericSymbols","vitaminFamilyRule","productAliasRules","aliasGroups","termAliases","Part2Error","UsageError","DataError","parseArgs","getSuspectProducts","selectReports","ageInYears","getGender","normalizeTerm","normalizeTerms","getProductLabel","readJson","checkDate","checkCount","checkTerms","getPagePath","loadManifest","checkReport","loadReports","iterValidatedPages","readJsonBytes"]
