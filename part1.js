const { mkdir, writeFile, rename } = require("node:fs/promises");
const { setTimeout: sleep } = require("node:timers/promises");
const path = require("node:path");

const apiUrl = "https://api.fda.gov/food/event.json";
const defaultStart = "20020101";
const defaultEnd = "20260101";
const pageLimit = 100;
const requestTimeout = 30_000;
const requestInterval = 300;
const maxRetries = 3;
const retryStatuses = new Set([429, 500, 502, 503, 504]);
const networkCodes = new Set([
  "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "EAI_AGAIN", "ENOTFOUND", "ENETUNREACH", "EHOSTUNREACH",
  "UND_ERR_CONNECT_TIMEOUT", "UND_ERR_HEADERS_TIMEOUT", "UND_ERR_BODY_TIMEOUT", "UND_ERR_SOCKET",
]);

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

function getWindows() {
  const windows = [];
  const firstYear = Number(defaultStart.slice(0, 4));
  const finalYear = Number(defaultEnd.slice(0, 4)) - 1;
  for (let year = firstYear; year <= finalYear; year++) {
    // Include the final January 1 in the preceding year's window, without overlap.
    windows.push({
      startDate: `${year}0101`,
      endDate: year === finalYear ? defaultEnd : `${year}1231`,
    });
  }
  return windows;
}

function getConcurrency(value = 3) {
  const limit = typeof value === "string" && /^\d+$/.test(value.trim()) ? Number(value) : value;
  if (!Number.isSafeInteger(limit) || limit < 1) {
    throw new Error("CONCURRENCY_LIMIT must be a positive integer.");
  }
  return limit;
}

function getPageName(pageNumber) {
  return `page-${String(pageNumber).padStart(6, "0")}.json`;
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

function getRetryDelay(value, now) {
  const text = value?.trim() || "";
  // Retry-After can be whole seconds or an HTTP date, never a fractional number.
  // Older HTTP dates omit the timezone but still mean GMT.
  const dateText = text.endsWith(" GMT") ? text : `${text} GMT`;
  const delay = /^\d+$/.test(text) ? Number(text) * 1000 :
    /^[A-Za-z]+,? /.test(text) ? Date.parse(dateText) - now : 0;
  return Number.isSafeInteger(delay) && delay > 0 ? delay : 0;
}

function isNetworkError(error) {
  if (!error) {
    return false;
  }
  return networkCodes.has(error.code) || isNetworkError(error.cause) ||
    (error instanceof AggregateError && error.errors.some(isNetworkError));
}

function createRequester({ fetchData = globalThis.fetch, now = Date.now, wait = sleep, random = Math.random, log = console.warn } = {}) {
  let queue = Promise.resolve();
  let nextStart = 0;
  let cooldown = 0;

  async function waitUntil(deadline) {
    while (deadline > now()) {
      // Long Retry-After values must not overflow Node's timer and fire immediately.
      await wait(Math.min(deadline - now(), 2_147_483_647));
    }
  }

  function waitForStart() {
    const turn = queue.then(async () => {
      // Another worker can extend the cooldown while this worker is asleep.
      while (Math.max(nextStart, cooldown) > now()) {
        await waitUntil(Math.max(nextStart, cooldown));
      }
      nextStart = now() + requestInterval;
    });
    queue = turn.catch(() => {});
    return turn;
  }

  return async function requestPage(url, label) {
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      await waitForStart();
      // Waiting for admission does not use up this attempt's network timeout.
      const signal = AbortSignal.timeout(requestTimeout);
      try {
        return await fetchPage(url, fetchData, signal);
      } catch (error) {
        const timedOut = error.name === "TimeoutError" || (signal.aborted && error.name === "AbortError");
        const temporary = error.status ? retryStatuses.has(error.status) : timedOut || isNetworkError(error);
        if (!temporary) {
          await error.response?.body?.cancel().catch(() => {});
          throw error;
        }
        const delay = Math.max(1000 * 2 ** attempt + Math.floor(random() * 250), getRetryDelay(error.retryAfter, now()));
        const retryAt = now() + delay;
        if (error.status === 429) {
          cooldown = Math.max(cooldown, retryAt);
        }
        // Publish the cooldown immediately, then release the unused HTTP error body.
        await error.response?.body?.cancel().catch(() => {});
        const reason = error.status ? `HTTP ${error.status}` : timedOut ? "request timeout" : "network failure";
        if (attempt === maxRetries) {
          throw new Error(`FDA request failed after ${attempt + 1} attempts (${reason}).`, { cause: error });
        }
        log(`${label}: ${reason}; retry ${attempt + 1}/${maxRetries} in at least ${delay} ms.`);
        // Backoff holds this worker, but does not block unrelated workers from the gate.
        await waitUntil(retryAt);
      }
    }
  };
}

async function fetchPage(url, fetchData, signal) {
  const response = await fetchData(url, {
    headers: { Accept: "application/json" },
    signal,
    redirect: "error",
  });
  if (!response.ok && response.status !== 404) {
    const error = new Error(`FDA request failed with HTTP ${response.status}.`);
    error.status = response.status;
    error.retryAfter = response.headers.get("retry-after");
    error.response = response;
    throw error;
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
  requestPage = createRequester({ fetchData }),
  runIds = new Set(),
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
    const { pageData, link, noMatches } = await requestPage(url, `${startDate}-${endDate} page ${pageCount + 1}`);
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
      if (runIds.has(report.report_number)) {
        throw new Error(`Duplicate report_number ${report.report_number} across windows.`);
      }
    }
    // Reserve IDs before awaiting the write so another worker cannot accept the same report.
    pageData.results.forEach(report => runIds.add(report.report_number));

    const fileName = getPageName(pageCount + 1);
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

async function downloadAll({
  outputDir = path.join(__dirname, "data"),
  concurrencyLimit = 3,
  apiKey,
  fetchData = globalThis.fetch,
  requestPage = createRequester({ fetchData }),
} = {}) {
  const limit = getConcurrency(concurrencyLimit);
  const windows = getWindows();
  await mkdir(path.dirname(outputDir), { recursive: true });
  await mkdir(outputDir);

  const manifestPath = path.join(outputDir, "manifest.json");
  const manifest = {
    schemaVersion: 1,
    complete: false,
    startDate: defaultStart,
    endDate: defaultEnd,
    startedAt: new Date().toISOString(),
    pageLimit,
    concurrencyLimit: limit,
    windowCount: windows.length,
    windows,
  };
  // A failed or interrupted run keeps this marker instead of appearing ready for analysis.
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, { flag: "wx" });

  const runIds = new Set();
  const results = new Array(windows.length);
  let nextIndex = 0;
  let failure = null;

  async function worker() {
    while (!failure && nextIndex < windows.length) {
      // Claim the index before awaiting so two workers cannot take the same window.
      const i = nextIndex;
      nextIndex += 1;
      const window = windows[i];
      try {
        results[i] = await downloadWindow({
          ...window,
          outputDir: path.join(outputDir, `${window.startDate}-${window.endDate}`),
          apiKey,
          requestPage,
          runIds,
        });
      } catch (error) {
        if (!failure) {
          failure = { window, error };
        }
        return;
      }
    }
  }

  // Workers catch failures themselves, allowing active windows to finish before exit.
  const workers = Array.from({ length: Math.min(limit, windows.length) }, () => worker());
  await Promise.all(workers);
  if (failure) {
    const { window, error } = failure;
    throw new Error(`Window ${window.startDate}-${window.endDate} failed: ${error.message}`, { cause: error });
  }

  const savedCount = results.reduce((sum, result) => sum + result.savedCount, 0);
  const total = results.reduce((sum, result) => sum + result.total, 0);
  const pageCount = results.reduce((sum, result) => sum + result.pageCount, 0);
  if (savedCount !== total || runIds.size !== total) {
    throw new Error(`Incomplete run: saved ${savedCount} reports with ${runIds.size} unique IDs; expected ${total}.`);
  }
  const summary = { windowCount: results.length, savedCount, total, pageCount, complete: true };
  const completedManifest = {
    ...manifest,
    ...summary,
    completedAt: new Date().toISOString(),
    uniqueCount: runIds.size,
    windows: results.map((result, i) => ({
      ...windows[i],
      savedCount: result.savedCount,
      total: result.total,
      pageCount: result.pageCount,
      files: Array.from({ length: result.pageCount }, (_, page) =>
        `${windows[i].startDate}-${windows[i].endDate}/${getPageName(page + 1)}`),
    })),
  };
  // Publish completion only after the full manifest is written; keep the old marker on failure.
  const tempPath = `${manifestPath}.tmp`;
  await writeFile(tempPath, `${JSON.stringify(completedManifest, null, 2)}\n`, { flag: "wx" });
  await rename(tempPath, manifestPath);
  return { outputDir, ...summary };
}

async function runDownload() {
  const concurrencyLimit = getConcurrency(process.env.CONCURRENCY_LIMIT);
  console.log(`Downloading ${defaultStart} through ${defaultEnd} with up to ${concurrencyLimit} concurrent requests.`);
  const result = await downloadAll({ concurrencyLimit, apiKey: process.env.OPENFDA_API_KEY });
  console.log(`Download complete: ${result.windowCount} windows, ${result.pageCount} pages, ${result.savedCount} reports.`);
  console.log(`Saved JSON files under ${result.outputDir}.`);
}

if (require.main === module) {
  runDownload().catch(error => {
    console.error(`Download failed; output may be incomplete: ${error.message}`);
    process.exitCode = 1;
  });
}

module.exports = { getWindows, getConcurrency, getPageUrl, getNextUrl, checkPage, getRetryDelay, createRequester, downloadWindow, downloadAll };
