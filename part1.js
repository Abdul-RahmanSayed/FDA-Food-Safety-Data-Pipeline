const { mkdir, writeFile } = require("node:fs/promises");
const path = require("node:path");

const apiUrl = "https://api.fda.gov/food/event.json";
const defaultStart = "20020101";
const defaultEnd = "20260101";
const pageLimit = 100;
const requestTimeout = 30_000;
const defaultFile = path.join(__dirname, "data", "first-page.json");

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

async function downloadPage({
  startDate = defaultStart,
  endDate = defaultEnd,
  outputFile = defaultFile,
  apiKey,
  fetchData = globalThis.fetch,
} = {}) {
  const url = getPageUrl(startDate, endDate, apiKey);
  const response = await fetchData(url, {
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(requestTimeout),
  });
  if (!response.ok) {
    throw new Error(`FDA request failed with HTTP ${response.status}.`);
  }

  let pageData;
  try {
    pageData = await response.json();
  } catch (error) {
    if (error instanceof SyntaxError) {
      throw new Error("FDA returned invalid JSON.", { cause: error });
    }
    throw error;
  }
  checkPage(pageData, startDate, endDate);

  await mkdir(path.dirname(outputFile), { recursive: true });
  // wx creates a new file and fails if it already exists.
  await writeFile(outputFile, `${JSON.stringify(pageData, null, 2)}\n`, { flag: "wx" });
  return { outputFile, savedCount: pageData.results.length, total: pageData.meta.results.total };
}

async function runDownload() {
  const result = await downloadPage({ apiKey: process.env.OPENFDA_API_KEY });
  console.log(`Saved ${result.savedCount} reports from one page to ${result.outputFile}.`);
  console.log(`FDA reports ${result.total} matches for the requested date range.`);
  console.log("This download is incomplete: pagination is not implemented yet.");
}

if (require.main === module) {
  runDownload().catch(error => {
    console.error(`Download failed: ${error.message}`);
    process.exitCode = 1;
  });
}

module.exports = { getPageUrl, checkPage, downloadPage };
