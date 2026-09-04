const { mkdir, writeFile } = require("node:fs/promises");
const path = require("node:path");

const apiUrl = "https://api.fda.gov/food/event.json";
const defaultStart = "20020101";
const defaultEnd = "20260101";
const pageLimit = 100;
const requestTimeout = 30_000;

function checkDate(value, label) {
  if (typeof value !== "string" || !/^\d{8}$/.test(value)) {
    throw new Error(`${label} must be a date in YYYYMMDD format.`);
  }

  const dateText = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;
  const date = new Date(`${dateText}T00:00:00Z`);
  // Dates like February 30 can roll forward, so compare with the original date.
  if (Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== dateText) {
    throw new Error(`${label} is not a valid calendar date: ${value}.`);
  }
}

function checkDates(startDate, endDate) {
  checkDate(startDate, "Start date");
  checkDate(endDate, "End date");
  // Valid YYYYMMDD strings sort in date order.
  if (startDate > endDate) {
    throw new Error("Start date must not be after end date.");
  }
}

function getPageUrl(startDate, endDate, apiKey) {
  checkDates(startDate, endDate);
  const url = new URL(apiUrl);
  url.searchParams.set("search", `date_started:[${startDate} TO ${endDate}]`);
  url.searchParams.set("limit", String(pageLimit));
  url.searchParams.set("sort", "date_started:asc");
  if (apiKey) {
    url.searchParams.set("api_key", apiKey);
  }
  return url;
}

function checkPage(pageData, startDate, endDate) {
  checkDates(startDate, endDate);
  if (!pageData || !Array.isArray(pageData.results)) {
    throw new Error("FDA response must contain a results array.");
  }
  if (pageData.results.length > pageLimit) {
    throw new Error(`FDA response contains more than ${pageLimit} reports.`);
  }

  const total = pageData.meta?.results?.total;
  if (!Number.isSafeInteger(total) || total < pageData.results.length) {
    throw new Error("FDA response has an invalid meta.results.total.");
  }

  // Different reports can share a date; use report numbers to find duplicates.
  const reportIds = new Set();
  for (let i = 0; i < pageData.results.length; i++) {
    const report = pageData.results[i];
    const label = `Report ${i + 1}`;
    if (typeof report?.report_number !== "string" || !report.report_number.trim()) {
      throw new Error(`${label} must have a nonempty report_number.`);
    }
    checkDate(report.date_started, `${label} date_started`);
    if (report.date_started < startDate || report.date_started > endDate) {
      throw new Error(`${label} date_started is outside ${startDate} through ${endDate}.`);
    }
    if (reportIds.has(report.report_number)) {
      throw new Error(`${label} repeats report_number ${report.report_number} within the page.`);
    }
    reportIds.add(report.report_number);
  }
}

function getNextUrl(link, startDate, endDate, apiKey) {
  if (!link) {
    return null;
  }

  let nextUrl = null;
  // A Link header may include other relations as well as next.
  for (const entry of link.split(/,\s*(?=<)/)) {
    const match = entry.match(/^\s*<([^<>]+)>\s*;(.*)$/);
    const relation = match?.[2].match(/(?:^|;)\s*rel\s*=\s*(?:"([^"]+)"|([^;\s]+))\s*(?:;|$)/i);
    if (!match || !relation) {
      throw new Error("FDA returned an invalid Link header.");
    }
    const relations = (relation[1] || relation[2]).toLowerCase().split(/\s+/);
    if (!relations.includes("next")) {
      continue;
    }
    if (nextUrl) {
      throw new Error("FDA returned multiple next-page links.");
    }
    try {
      nextUrl = new URL(match[1]);
    } catch {
      throw new Error("FDA returned an invalid next-page URL.");
    }
  }
  if (!nextUrl) {
    return null;
  }

  const firstUrl = getPageUrl(startDate, endDate);
  if (nextUrl.origin !== firstUrl.origin || nextUrl.pathname !== firstUrl.pathname ||
      nextUrl.username || nextUrl.password || nextUrl.hash) {
    throw new Error("Next-page link must use the FDA food-event endpoint.");
  }
  for (const name of ["search", "limit", "sort"]) {
    if (nextUrl.searchParams.getAll(name).length !== 1 ||
        nextUrl.searchParams.get(name) !== firstUrl.searchParams.get(name)) {
      throw new Error(`Next-page link changed the required ${name} parameter.`);
    }
  }
  if (nextUrl.searchParams.getAll("search_after").length !== 1 ||
      !nextUrl.searchParams.get("search_after")) {
    throw new Error("Next-page link must contain one nonempty search_after cursor.");
  }
  // FDA's supplied cursor links can include skip=0; ordinary offset paging is not used.
  if (nextUrl.searchParams.getAll("skip").length > 1 ||
      (nextUrl.searchParams.has("skip") && nextUrl.searchParams.get("skip") !== "0")) {
    throw new Error("Next-page link must not use a nonzero skip.");
  }
  if (nextUrl.searchParams.has("count")) {
    throw new Error("Next-page link must retrieve reports, not aggregate counts.");
  }
  if (apiKey) {
    nextUrl.searchParams.set("api_key", apiKey);
  }
  return nextUrl;
}

async function fetchPage(url, fetchData) {
  const response = await fetchData(url, {
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(requestTimeout),
    redirect: "error",
  });
  if (!response.ok && response.status !== 404) {
    throw new Error(`FDA request failed with HTTP ${response.status}.`);
  }

  let pageData;
  try {
    pageData = await response.json();
  } catch (error) {
    if (error instanceof SyntaxError) {
      if (!response.ok) {
        throw new Error(`FDA request failed with HTTP ${response.status} and an invalid JSON error response.`);
      }
      throw new Error("FDA returned invalid JSON.", { cause: error });
    }
    throw error;
  }

  const link = response.headers.get("link");
  if (!response.ok) {
    if (pageData?.error?.code !== "NOT_FOUND" || pageData.error.message !== "No matches found!") {
      throw new Error(`FDA request failed with HTTP ${response.status}.`);
    }
    return { pageData, link, noMatches: true };
  }
  return { pageData, link, noMatches: false };
}

async function downloadWindow({
  startDate = defaultStart,
  endDate = defaultEnd,
  outputDir = path.join(__dirname, "data", `${startDate}-${endDate}`),
  apiKey,
  fetchData = globalThis.fetch,
} = {}) {
  let url = getPageUrl(startDate, endDate, apiKey);
  // Claim a fresh directory before fetching so separate runs cannot mix their pages.
  await mkdir(path.dirname(outputDir), { recursive: true });
  await mkdir(outputDir);

  const reportIds = new Set();
  const cursors = new Set();
  let total = null;
  let savedCount = 0;
  let pageCount = 0;

  while (url) {
    cursors.add(url.searchParams.get("search_after"));
    const { pageData, link, noMatches } = await fetchPage(url, fetchData);
    const nextUrl = getNextUrl(link, startDate, endDate, apiKey);
    if (nextUrl && cursors.has(nextUrl.searchParams.get("search_after"))) {
      throw new Error("FDA repeated a pagination cursor.");
    }

    if (noMatches) {
      if (nextUrl) {
        throw new Error("FDA returned no matches with a next-page link.");
      }
      // FDA can send one extra cursor after a full final page. Counts must still match.
      total = total === null ? 0 : total;
      url = null;
      continue;
    }

    checkPage(pageData, startDate, endDate);
    const pageTotal = pageData.meta.results.total;
    if (total !== null && pageTotal !== total) {
      throw new Error(`FDA total changed from ${total} to ${pageTotal} during the window.`);
    }
    total = pageTotal;
    if (savedCount + pageData.results.length > total) {
      throw new Error("FDA returned more reports than the expected window total.");
    }
    if (pageData.results.length === 0 && nextUrl) {
      throw new Error("FDA returned an empty page with a next-page link.");
    }
    for (const report of pageData.results) {
      if (reportIds.has(report.report_number)) {
        throw new Error(`Duplicate report_number ${report.report_number} across pages.`);
      }
    }

    const fileName = `page-${String(pageCount + 1).padStart(6, "0")}.json`;
    // wx also protects against an unexpected file appearing during this run.
    await writeFile(path.join(outputDir, fileName), `${JSON.stringify(pageData, null, 2)}\n`, { flag: "wx" });
    pageData.results.forEach(report => reportIds.add(report.report_number));
    savedCount += pageData.results.length;
    pageCount += 1;
    // Even a short page at the final date can have more reports behind its cursor.
    url = nextUrl;
  }

  if (savedCount !== total || reportIds.size !== total) {
    throw new Error(`Incomplete window: saved ${savedCount} unique reports; expected ${total}.`);
  }
  return { outputDir, savedCount, total, pageCount, complete: true };
}

async function runDownload() {
  const startDate = defaultStart;
  const endDate = "20021231";
  console.log(`Downloading one window: ${startDate} through ${endDate}.`);
  const result = await downloadWindow({ startDate, endDate, apiKey: process.env.OPENFDA_API_KEY });
  console.log(`Window complete: saved ${result.savedCount} reports in ${result.pageCount} pages to ${result.outputDir}.`);
  console.log("The full assignment range is not complete: yearly workers are not implemented yet.");
}

if (require.main === module) {
  runDownload().catch(error => {
    console.error(`Window download failed; output may be incomplete: ${error.message}`);
    process.exitCode = 1;
  });
}

module.exports = { getPageUrl, getNextUrl, checkPage, downloadWindow };
