const TOKEN_KEY = "flightpricer.githubToken";
const DEFAULT_REPO = "simonrisman/flightpricer";
const DAY_OPTIONS = [
  ["mon", "Mon"],
  ["tue", "Tue"],
  ["wed", "Wed"],
  ["thu", "Thu"],
  ["fri", "Fri"],
  ["sat", "Sat"],
  ["sun", "Sun"],
];
const ORIGIN_CHOICES = ["JFK", "EWR", "BOS"];
const AIRPORTS = {
  JFK: "New York JFK",
  EWR: "Newark",
  LGA: "New York LGA",
  BOS: "Boston",
  BOG: "Bogotá",
  MDE: "Medellín",
  LIM: "Lima",
  EZE: "Buenos Aires",
  CDG: "Paris",
  MAD: "Madrid",
  BCN: "Barcelona",
  HND: "Tokyo Haneda",
  NRT: "Tokyo Narita",
  SGN: "Ho Chi Minh City",
  HAN: "Hanoi",
  BKK: "Bangkok",
  SIN: "Singapore",
  TBS: "Tbilisi",
  PDL: "Ponta Delgada",
  FNC: "Funchal",
};

const state = {
  config: null,
  latest: { routes: [], currency: "USD" },
  status: null,
  histories: {},
  sorts: {},
  queries: {},
  focus: null,
  open: {},
  openFares: {},
};

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(function (pair) {
    const key = pair[0];
    const value = pair[1];
    if (value == null || value === false) return;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  });
  (children || []).forEach(function (child) {
    if (child == null || child === false) return;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  });
  return node;
}

function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch (err) {
    return "";
  }
}

function repoOf(config) {
  return (config && config.settings && config.settings.github_repo) || DEFAULT_REPO;
}

function currencyOf() {
  return (state.latest && state.latest.currency) || (state.config && state.config.settings && state.config.settings.currency) || "USD";
}

function money(amount) {
  if (amount == null || Number.isNaN(Number(amount))) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currencyOf(),
    maximumFractionDigits: 0,
  }).format(amount);
}

function dateParts(iso) {
  const parts = String(iso || "").slice(0, 10).split("-").map(Number);
  if (parts.length < 3 || !parts[0]) return null;
  return new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
}

function prettyDate(iso) {
  const when = dateParts(iso);
  if (!when) return iso ? String(iso) : "—";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(when);
}

function weekdayLabel(iso, style) {
  const when = dateParts(iso);
  if (!when) return "";
  return new Intl.DateTimeFormat("en-US", { weekday: style || "long", timeZone: "UTC" }).format(when);
}

function clock(value) {
  const match = String(value || "").match(/^(\d{1,2}):(\d{2})/);
  if (!match) return "";
  let hour = Number(match[1]);
  const suffix = hour >= 12 ? "PM" : "AM";
  hour = hour % 12 || 12;
  return hour + ":" + match[2] + " " + suffix;
}

function formatWhen(iso) {
  if (!iso) return "No successful update yet";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "No successful update yet";
  const absolute = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(when);
  return absolute + " (" + relativeTime(when) + ")";
}

function formatWhenShort(iso) {
  if (!iso) return "Never";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "Never";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(when);
}

function relativeTime(when) {
  const minutes = Math.round((Date.now() - when.getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return minutes + " min ago";
  const hours = Math.round(minutes / 60);
  if (hours < 36) return hours + " hr ago";
  return Math.round(hours / 24) + " days ago";
}

function place(code) {
  return AIRPORTS[code] ? AIRPORTS[code] + " (" + code + ")" : code;
}

function stopsLabel(stops) {
  if (stops == null) return "—";
  if (stops === 0) return "Nonstop";
  if (stops === 1) return "1 stop";
  return stops + " stops";
}

function rowId(key) {
  return "row-" + String(key).replaceAll("|", "--");
}

function findRow(latest, key) {
  const routes = (latest && latest.routes) || [];
  for (let i = 0; i < routes.length; i += 1) {
    const searches = routes[i].searches || [];
    for (let j = 0; j < searches.length; j += 1) {
      if (searches[j].key === key) return searches[j];
    }
  }
  return null;
}

function latestRoute(id) {
  return ((state.latest && state.latest.routes) || []).find(function (route) {
    return route.id === id;
  });
}

function routeRows(route) {
  const saved = latestRoute(route.id);
  return (saved && saved.searches) || [];
}

async function loadJson(url) {
  const response = await fetch(url + (url.includes("?") ? "&" : "?") + "t=" + Date.now(), { cache: "no-store" });
  if (!response.ok) return null;
  return response.json();
}

async function loadConfig() {
  const local = location.hostname === "localhost" || location.hostname === "127.0.0.1";
  if (!local && window.jsyaml) {
    try {
      const response = await fetch(
        "https://raw.githubusercontent.com/" + DEFAULT_REPO + "/main/config.yaml?t=" + Date.now(),
        { cache: "no-store" }
      );
      if (response.ok) {
        const parsed = jsyaml.load(await response.text(), { schema: jsyaml.CORE_SCHEMA });
        if (parsed && parsed.routes) return normalizeConfig(parsed);
      }
    } catch (err) {
      /* fall through to the copy published with the site */
    }
  }
  const fallback = await loadJson("data/config.json");
  if (!fallback) throw new Error("config.json is missing");
  return normalizeConfig(fallback);
}

function toBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  bytes.forEach(function (byte) {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary);
}

function fromBase64(text) {
  const binary = atob(text.replace(/\n/g, ""));
  const bytes = Uint8Array.from(binary, function (char) {
    return char.charCodeAt(0);
  });
  return new TextDecoder().decode(bytes);
}

async function gh(path, token, options) {
  const response = await fetch("https://api.github.com" + path, {
    method: (options && options.method) || "GET",
    body: options && options.body,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: "Bearer " + token,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
  });
  if (response.status === 204) return null;
  const text = await response.text();
  if (!response.ok) throw new Error(response.status + " " + text.slice(0, 280));
  return text ? JSON.parse(text) : null;
}

async function saveFile(token, repo, path, contents, message) {
  let sha;
  try {
    const current = await gh("/repos/" + repo + "/contents/" + path + "?ref=main", token);
    sha = current.sha;
  } catch (err) {
    if (!String(err.message).startsWith("404")) throw err;
  }
  const body = { message: message, content: toBase64(contents), branch: "main" };
  if (sha) body.sha = sha;
  await gh("/repos/" + repo + "/contents/" + path, token, { method: "PUT", body: JSON.stringify(body) });
}

async function saveConfig(message) {
  const token = getToken();
  if (!token) {
    openTokenDialog();
    throw new Error("Add a GitHub token to save changes.");
  }
  const config = normalizeConfig(state.config);
  state.config = config;
  const repo = repoOf(config);
  const yaml = emitYaml(config);
  await saveFile(token, repo, "config.yaml", yaml, message);
  await saveFile(token, repo, "site/data/config.json", JSON.stringify(config, null, 2) + "\n", message);
}

async function fetchPublishedJson(token, repo, path) {
  try {
    const payload = await gh("/repos/" + repo + "/contents/" + path + "?ref=main", token);
    return JSON.parse(fromBase64(payload.content));
  } catch (err) {
    const response = await fetch(
      "https://raw.githubusercontent.com/" + repo + "/main/" + path + "?t=" + Date.now(),
      { cache: "no-store" }
    );
    if (!response.ok) throw err;
    return response.json();
  }
}

function sleep(ms) {
  return new Promise(function (resolve) {
    setTimeout(resolve, ms);
  });
}

function isNewer(next, previous) {
  if (!next) return false;
  if (!previous) return true;
  return Date.parse(next) > Date.parse(previous);
}

async function pollForKey(repo, token, key, before) {
  const deadline = Date.now() + 12 * 60 * 1000;
  while (Date.now() < deadline) {
    await sleep(12000);
    try {
      const latest = await fetchPublishedJson(token, repo, "site/data/latest.json");
      const status = await fetchPublishedJson(token, repo, "site/data/status.json");
      const row = findRow(latest, key);
      if (row && !row.stale && isNewer(row.fetched_at, before && before.fetched_at)) {
        return { latest: latest, status: status, row: row };
      }
      if (row && row.stale && isNewer(row.last_attempt_at, before && before.last_attempt_at)) {
        return {
          latest: latest,
          status: status,
          row: row,
          error: row.error || "The last attempt failed. The price shown is the previous one.",
        };
      }
      const errors = (status && status.errors) || [];
      const mentioned = errors.some(function (item) {
        return String(item).indexOf(key) !== -1;
      });
      if (mentioned && status && isNewer(status.last_run, before && before.seen_run)) {
        return { latest: latest, status: status, row: row, error: errors.join("\n") };
      }
    } catch (err) {
      /* the commit may not be visible yet */
    }
  }
  return { error: "Timed out waiting for the scraper. It may still be queued behind another run." };
}

function setAction(message) {
  document.getElementById("action-msg").textContent = message || "";
}

function openTokenDialog() {
  const input = document.getElementById("token-input");
  input.value = "";
  document.getElementById("token-state").textContent = getToken()
    ? "A token is already saved in this browser. Paste a new one to replace it."
    : "No token saved in this browser yet.";
  document.getElementById("token-dialog").showModal();
}

function syncTokenUi() {
  document.getElementById("run-full").hidden = !getToken();
}

function readRouteForm(form, route) {
  const next = Object.assign({}, route);
  next.depart_window = {
    start: form.elements.start.value,
    end: form.elements.end.value,
  };
  const days = Array.from(form.querySelectorAll('input[name="day"]:checked')).map(function (box) {
    return box.value;
  });
  next.depart_days = days.length === DAY_OPTIONS.length ? null : days;
  next.origins = Array.from(form.querySelectorAll('input[name="origin"]:checked')).map(function (box) {
    return box.value;
  });
  next.destinations = form.elements.destinations.value
    .split(/[^A-Za-z]+/)
    .map(function (code) {
      return code.toUpperCase();
    })
    .filter(function (code, index, all) {
      return /^[A-Z]{3}$/.test(code) && all.indexOf(code) === index;
    });
  const lengths = form.elements.lengths.value.match(/\d+/g) || [];
  next.trip_length_days = lengths
    .map(Number)
    .filter(function (length, index, all) {
      return length >= 1 && length <= 60 && all.indexOf(length) === index;
    });
  next.target_price = null;
  next.active = form.elements.active.checked;
  return next;
}

function validateRoute(route) {
  if (!route.depart_window.start || !route.depart_window.end) return "Choose a start and end date.";
  if (route.depart_window.start > route.depart_window.end) return "The window start is after the end.";
  if (route.depart_days && !route.depart_days.length) return "Pick at least one weekday.";
  if (!route.origins.length) return "Pick at least one origin airport.";
  if (!route.destinations.length) return "Enter at least one destination airport.";
  if (route.trip === "round_trip" && !route.trip_length_days.length) return "Enter at least one trip length.";
  return "";
}

function applyAllForms() {
  if (!state.config) return "";
  for (let i = 0; i < state.config.routes.length; i += 1) {
    const route = state.config.routes[i];
    const form = document.getElementById("form-" + route.id);
    if (!form) continue;
    const next = readRouteForm(form, route);
    const error = validateRoute(next);
    if (error) return route.label + ": " + error;
    next.extra_dates = route.extra_dates || [];
    state.config.routes[i] = next;
  }
  return "";
}

function queryFromForm(route) {
  const form = document.getElementById("query-" + route.id);
  const saved = state.queries[route.id];
  const depart = (form && form.elements.depart.value) || (saved && saved.depart) || route.depart_window.start;
  const length = Number((form && form.elements.nights.value) || (saved && saved.length) || (route.trip_length_days || [7])[0]);
  let ret = form && form.elements["return"].value;
  if (!ret) ret = saved && saved.ret;
  if (!ret) ret = addDays(depart, length || 7);
  return {
    origin: (form && form.elements.origin.value) || (saved && saved.origin) || route.origins[0],
    dest: (form && form.elements.dest.value) || (saved && saved.dest) || route.destinations[0],
    depart: depart,
    ret: ret,
    length: String(length || daysBetween(depart, ret)),
    keep: form ? form.elements.keep.checked : saved ? saved.keep : true,
  };
}

function paintPreview(route) {
  const box = document.getElementById("preview-" + route.id);
  if (!box) return;
  const query = queryFromForm(route);
  state.queries[route.id] = query;
  box.replaceChildren();
  if (!query.depart || !query.ret) {
    box.append(el("p", { class: "query-msg" }, ["Choose a departure and a return date."]));
    return;
  }
  if (query.ret <= query.depart) {
    box.append(el("p", { class: "query-msg" }, ["Return has to be after departure."]));
    return;
  }
  const key = searchKey(route.id, query.origin, query.dest, query.depart, query.ret);
  const row = findRow(state.latest, key);
  const watched = inWindow(route, query.depart, query.ret);
  if (!row) {
    box.append(
      el("p", { class: "query-msg" }, [
        watched
          ? "This date is inside the watched window, but it has not succeeded yet."
          : "This date is outside the watched window.",
      ]),
      el("p", { class: "when" }, ["Last successful update: never"])
    );
    return;
  }
  box.append(
    el("p", { class: "query-price" }, [money(row.price)]),
    el("p", { class: "query-msg" }, [
      (row.airline || "—") + " · " + stopsLabel(row.stops) + " · " + (row.duration || "—"),
    ]),
    el("p", { class: "when" }, ["Last successful update: " + formatWhen(row.fetched_at)])
  );
  if (row.stale) {
    box.append(el("p", { class: "query-msg" }, ["The last attempt failed. This is the previous price."]));
  }
}

function successRange(rows) {
  const stamps = rows
    .map(function (row) {
      return row.fetched_at;
    })
    .filter(Boolean)
    .sort();
  return { oldest: stamps[0] || null, newest: stamps[stamps.length - 1] || null };
}

function renderHeader() {
  const title = (state.config && state.config.settings && state.config.settings.dashboard_title) || "Flight Pricer";
  document.getElementById("title").textContent = title;
  document.title = title;
  const status = state.status || {};
  const runLine = document.getElementById("run-line");
  const successLine = document.getElementById("success-line");
  const pill = document.getElementById("pill");
  if (!status.last_run) {
    runLine.textContent = "No scrape has run yet.";
    successLine.textContent = "Last successful run: never";
    pill.textContent = "No runs yet";
    pill.className = "pill never";
  } else {
    runLine.textContent = "Last run: " + formatWhen(status.last_run);
    successLine.textContent = "Last successful run: " + formatWhen(status.last_success);
    const ageHours = (Date.now() - Date.parse(status.last_run)) / 36e5;
    let label = status.state || "unknown";
    let kind = status.state || "never";
    if (ageHours > 36) {
      label = "over 36h old";
      kind = "old";
    } else if (status.state === "ok") label = "ok";
    else if (status.state === "degraded") label = "degraded";
    else if (status.state === "failed") label = "failed";
    pill.textContent = label;
    pill.className = "pill " + kind;
  }
  const coverage = document.getElementById("coverage");
  const cov = status.coverage;
  const cap = state.config && state.config.settings && state.config.settings.max_requests_per_run;
  let note = "Each city shows its cheapest saved fare, the trip dates for that fare, and when that price was fetched.";
  if (cap) {
    note += " JFK, Newark, and Boston each get their own run through the day, " + cap.toLocaleString("en-US") + " searches at a time.";
  } else if (cov && cov.total > cov.scheduled) {
    note += " A daily run refreshes " + cov.scheduled + " of " + cov.total + " searches, then continues through the rest.";
  }
  coverage.textContent = note;
}

function compareRows(a, b, key, dir) {
  const left = a[key];
  const right = b[key];
  const leftMissing = left == null || left === "";
  const rightMissing = right == null || right === "";
  if (leftMissing && rightMissing) return 0;
  if (leftMissing) return 1;
  if (rightMissing) return -1;
  let order = 0;
  if (typeof left === "number" && typeof right === "number") order = left - right;
  else order = String(left).localeCompare(String(right));
  return dir === "desc" ? -order : order;
}

function tripWhen(row) {
  const depart = weekdayLabel(row.depart, "short") + ", " + prettyDate(row.depart);
  if (!row["return"]) return depart;
  return depart + " – " + weekdayLabel(row["return"], "short") + ", " + prettyDate(row["return"]);
}

function detailRows(row) {
  const deltaText =
    row.delta == null
      ? "—"
      : (row.delta < 0 ? "↓ " : row.delta > 0 ? "↑ " : "") + money(Math.abs(row.delta)).replace("US", "");
  const best = row.best_ever == null ? "—" : money(row.best_ever) + (row.best_ever_date ? " · " + prettyDate(row.best_ever_date) : "");
  return [
    ["Price", money(row.price) + (row.stale ? " · last attempt failed" : "")],
    ["Departure", weekdayLabel(row.depart) + ", " + prettyDate(row.depart)],
    ["Departs", clock(row.depart_time) || "Time not saved yet"],
    ["Arrives", clock(row.arrive_time) || "—"],
    ["Return", row["return"] ? weekdayLabel(row["return"]) + ", " + prettyDate(row["return"]) : "—"],
    ["From", place(row.origin)],
    ["To", place(row.dest)],
    ["Airline", row.airline || "—"],
    ["Stops", stopsLabel(row.stops)],
    ["Duration", row.duration || "—"],
    ["Change", deltaText],
    ["Best", best],
    ["Updated", formatWhen(row.fetched_at)],
  ];
}

function renderFareCard(row) {
  const open = !!state.openFares[row.key];
  const card = el("article", { class: "fare" + (row.stale ? " stale" : ""), id: rowId(row.key) });
  const toggle = el(
    "button",
    { type: "button", class: "fare-toggle", "aria-expanded": open ? "true" : "false" },
    [
      el("strong", { class: "fare-price" }, [money(row.price)]),
      el("span", { class: "fare-when" }, [tripWhen(row)]),
      el("span", { class: "fare-meta" }, [
        row.origin + " → " + row.dest + " · Departs " + (clock(row.depart_time) || "time unavailable"),
      ]),
    ]
  );
  const detail = el("div", { class: "fare-body" });
  if (!open) detail.hidden = true;
  const facts = el("dl", { class: "fare-detail" });
  detailRows(row).forEach(function (pair) {
    facts.append(el("dt", {}, [pair[0]]), el("dd", {}, [pair[1]]));
  });
  const links = el("div", { class: "links fare-links" }, [
    row.google_url
      ? el("a", { href: row.google_url, target: "_blank", rel: "noopener noreferrer" }, ["Google Flights"])
      : null,
    el("a", { href: "https://travel.capitalone.com/", target: "_blank", rel: "noopener noreferrer" }, ["Capital One"]),
  ]);
  detail.append(facts, links);
  toggle.addEventListener("click", function () {
    const willOpen = detail.hidden;
    detail.hidden = !willOpen;
    state.openFares[row.key] = willOpen;
    toggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
  });
  card.append(toggle, detail);
  return card;
}

function renderFareList(route, rows) {
  const sort = state.sorts[route.id] || { key: "price", dir: "asc" };
  const sorted = rows.slice().sort(function (a, b) {
    return compareRows(a, b, sort.key, sort.dir);
  });
  const columns = [
    ["price", "Price"],
    ["depart", "Date"],
    ["depart_time", "Departure time"],
  ];
  const bar = el(
    "div",
    { class: "fare-sort" },
    columns.map(function (column) {
      const active = sort.key === column[0];
      const arrow = active ? (sort.dir === "asc" ? " ↑" : " ↓") : "";
      return el("button", { type: "button", class: "secondary" + (active ? " active" : "") }, [column[1] + arrow]);
    })
  );
  const list = el("div", { class: "fare-list", id: "fares-" + route.id }, [bar].concat(sorted.map(renderFareCard)));
  bar.querySelectorAll("button").forEach(function (button, index) {
    button.addEventListener("click", function () {
      const key = columns[index][0];
      const current = state.sorts[route.id] || { key: "price", dir: "asc" };
      state.sorts[route.id] = {
        key: key,
        dir: current.key === key && current.dir === "asc" ? "desc" : "asc",
      };
      const holder = document.getElementById("fares-" + route.id);
      holder.replaceWith(renderFareList(route, rows));
    });
  });
  return list;
}

function cheapestByDay(history) {
  const map = new Map();
  ((history && history.points) || []).forEach(function (point) {
    const previous = map.get(point.date);
    if (previous == null || point.price < previous) map.set(point.date, point.price);
  });
  return Array.from(map.entries()).sort(function (a, b) {
    return a[0].localeCompare(b[0]);
  });
}

function drawSparkline(canvas, points) {
  const width = canvas.clientWidth || 600;
  const height = 72;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, width, height);
  if (!points.length) return;
  const prices = points.map(function (point) {
    return point[1];
  });
  const min = Math.min.apply(null, prices);
  const max = Math.max.apply(null, prices);
  const span = max - min || 1;
  ctx.beginPath();
  points.forEach(function (point, index) {
    const x = points.length === 1 ? width / 2 : (index / (points.length - 1)) * (width - 8) + 4;
    const y = height - 16 - ((point[1] - min) / span) * (height - 28);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#1f4d40";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.fillStyle = "#5c675f";
  ctx.font = "12px Outfit, sans-serif";
  ctx.fillText(money(min), 4, height - 2);
  ctx.fillText(money(max), width - 72, 12);
}

function dayChecks(route) {
  const selected = new Set(route.depart_days && route.depart_days.length ? route.depart_days : DAY_OPTIONS.map(function (day) { return day[0]; }));
  return el(
    "div",
    { class: "checks" },
    DAY_OPTIONS.map(function (day) {
      return el("label", {}, [
        el("input", { type: "checkbox", name: "day", value: day[0], checked: selected.has(day[0]) ? "checked" : null }),
        day[1],
      ]);
    })
  );
}

function originChecks(route) {
  const choices = ORIGIN_CHOICES.slice();
  (route.origins || []).forEach(function (code) {
    if (choices.indexOf(code) === -1) choices.push(code);
  });
  return el(
    "div",
    { class: "checks" },
    choices.map(function (code) {
      return el("label", { title: place(code) }, [
        el("input", {
          type: "checkbox",
          name: "origin",
          value: code,
          checked: route.origins.indexOf(code) !== -1 ? "checked" : null,
        }),
        code,
      ]);
    })
  );
}

function renderEditor(route) {
  const form = el("form", { id: "form-" + route.id, class: "panel" });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
  });
  const error = el("p", { class: "form-error", id: "error-" + route.id });
  const extras = el("ul", { class: "extra-list" });
  (route.extra_dates || []).forEach(function (extra, index) {
    extras.append(
      el("li", {}, [
        el("span", {}, [
          (extra.origin || "any origin") +
            " " +
            (extra.dest || "any dest") +
            " · " +
            prettyDate(extra.depart) +
            (extra["return"] ? " – " + prettyDate(extra["return"]) : ""),
        ]),
        el("button", { type: "button", class: "secondary" }, ["Remove"]),
      ])
    );
    extras.lastChild.querySelector("button").addEventListener("click", function () {
      route.extra_dates.splice(index, 1);
      render();
    });
  });
  form.append(
    el("h3", {}, ["Watched window"]),
    el("div", { class: "fields" }, [
      el("label", {}, ["Start", el("input", { type: "date", name: "start", value: route.depart_window.start, required: "required" })]),
      el("label", {}, ["End", el("input", { type: "date", name: "end", value: route.depart_window.end, required: "required" })]),
      el("label", {}, ["Trip lengths (days)", el("input", { name: "lengths", value: (route.trip_length_days || []).join(", ") })]),
      el("label", { class: "span-2" }, ["Destination airports", el("input", { name: "destinations", value: (route.destinations || []).join(", ") })]),
    ]),
    el("p", { class: "meta" }, ["Departure days"]),
    dayChecks(route),
    el("p", { class: "meta" }, ["Origins"]),
    originChecks(route),
    el("label", { class: "inline-check" }, [
      el("input", { type: "checkbox", name: "active", checked: route.active !== false ? "checked" : null }),
      "Keep this route active",
    ]),
    error
  );
  if (extras.childNodes.length) {
    form.append(el("p", { class: "meta" }, ["Extra dates outside the window"]), extras);
  }
  form.append(
    el("div", { class: "row-actions" }, [
      el("button", { type: "button", class: "primary", id: "save-" + route.id }, ["Save window"]),
      el("button", { type: "button", class: "secondary", id: "download-" + route.id }, ["Download YAML"]),
    ])
  );
  form.querySelector("#save-" + route.id).addEventListener("click", async function () {
    error.textContent = "";
    const problem = applyAllForms();
    if (problem) {
      error.textContent = problem;
      return;
    }
    const button = form.querySelector("#save-" + route.id);
    button.disabled = true;
    try {
      await saveConfig("config: update search windows");
      setAction("Saved. The next scrape uses these windows.");
      render();
    } catch (err) {
      error.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  });
  form.querySelector("#download-" + route.id).addEventListener("click", function () {
    const problem = applyAllForms();
    if (problem) {
      error.textContent = problem;
      return;
    }
    const blob = new Blob([emitYaml(state.config)], { type: "text/yaml" });
    const link = el("a", { href: URL.createObjectURL(blob), download: "config.yaml" });
    document.body.append(link);
    link.click();
    link.remove();
  });
  return form;
}

function renderQuery(route) {
  const saved = state.queries[route.id];
  const defaultLength = (route.trip_length_days || [7])[0];
  const depart = (saved && saved.depart) || route.depart_window.start;
  const ret = (saved && saved.ret) || addDays(depart, defaultLength);
  const form = el("form", { id: "query-" + route.id, class: "panel" });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
  });
  const lengths = route.trip_length_days || [defaultLength];
  const preview = el("div", { class: "query-result", id: "preview-" + route.id });
  const message = el("p", { class: "form-error", id: "query-msg-" + route.id });
  form.append(
    el("h3", {}, ["Look up a date"]),
    el("div", { class: "fields" }, [
      el(
        "label",
        {},
        [
          "From",
          el(
            "select",
            { name: "origin" },
            route.origins.map(function (code) {
              return el("option", { value: code, selected: saved && saved.origin === code ? "selected" : null }, [code]);
            })
          ),
        ]
      ),
      el(
        "label",
        {},
        [
          "To",
          el(
            "select",
            { name: "dest" },
            route.destinations.map(function (code) {
              return el("option", { value: code, selected: saved && saved.dest === code ? "selected" : null }, [code]);
            })
          ),
        ]
      ),
      el("label", {}, ["Depart", el("input", { type: "date", name: "depart", value: depart, required: "required" })]),
      el("label", {}, ["Return", el("input", { type: "date", name: "return", value: ret, required: "required" })]),
      el(
        "label",
        {},
        [
          "Trip length",
          el(
            "select",
            { name: "nights" },
            lengths.map(function (length) {
              return el(
                "option",
                { value: String(length), selected: String(length) === String(daysBetween(depart, ret)) ? "selected" : null },
                [length + " days"]
              );
            })
          ),
        ]
      ),
    ]),
    preview,
    el("label", { class: "inline-check" }, [
      el("input", { type: "checkbox", name: "keep", checked: !saved || saved.keep ? "checked" : null }),
      "Keep watching this date if it is outside the window",
    ]),
    message,
    el("div", { class: "query-actions" }, [
      el("button", { type: "button", class: "primary", id: "fetch-" + route.id }, ["Fetch this date"]),
    ])
  );
  const departInput = form.elements.depart;
  const returnInput = form.elements["return"];
  const lengthInput = form.elements.nights;
  lengthInput.addEventListener("change", function () {
    if (departInput.value) returnInput.value = addDays(departInput.value, Number(lengthInput.value));
    paintPreview(route);
  });
  departInput.addEventListener("input", function () {
    if (departInput.value && lengthInput.value) returnInput.value = addDays(departInput.value, Number(lengthInput.value));
  });
  returnInput.addEventListener("input", function () {
    const length = daysBetween(departInput.value, returnInput.value);
    const match = Array.from(lengthInput.options).find(function (option) {
      return Number(option.value) === length;
    });
    if (match) lengthInput.value = match.value;
    paintPreview(route);
  });
  form.querySelectorAll("input, select").forEach(function (control) {
    control.addEventListener("input", function () {
      paintPreview(route);
    });
  });
  form.querySelector("#fetch-" + route.id).addEventListener("click", function () {
    fetchDate(route, message);
  });
  return form;
}

async function fetchDate(route, message) {
  message.textContent = "";
  const problem = applyAllForms();
  if (problem) {
    message.textContent = problem;
    return;
  }
  const current = state.config.routes.find(function (item) {
    return item.id === route.id;
  });
  const query = queryFromForm(current);
  if (!query.depart || !query.ret || query.ret <= query.depart) {
    message.textContent = "Choose a departure and a later return date.";
    return;
  }
  const key = searchKey(current.id, query.origin, query.dest, query.depart, query.ret);
  const button = document.getElementById("fetch-" + current.id);
  button.disabled = true;
  try {
    if (query.keep && !inWindow(current, query.depart, query.ret)) {
      current.extra_dates = current.extra_dates || [];
      const entry = { depart: query.depart, return: query.ret, origin: query.origin, dest: query.dest };
      const exists = current.extra_dates.some(function (extra) {
        return extra.depart === entry.depart && extra["return"] === entry["return"] && extra.origin === entry.origin && extra.dest === entry.dest;
      });
      if (!exists) current.extra_dates.push(entry);
      message.textContent = "Saving this date onto the route…";
      await saveConfig("config: watch " + current.id + " " + query.depart);
    }
    const token = getToken();
    if (!token) {
      openTokenDialog();
      message.textContent = "A GitHub token is required to fetch a date.";
      return;
    }
    const before = findRow(state.latest, key);
    const seenRun = state.status && state.status.last_run;
    message.textContent = "Queued on GitHub Actions. A date query usually finishes in a few minutes unless a full scrape is already running.";
    await gh("/repos/" + repoOf(state.config) + "/actions/workflows/scrape.yml/dispatches", token, {
      method: "POST",
      body: JSON.stringify({
        ref: "main",
        inputs: {
          mode: "adhoc",
          route_id: current.id,
          origin: query.origin,
          dest: query.dest,
          depart: query.depart,
          return_date: query.ret,
        },
      }),
    });
    const result = await pollForKey(repoOf(state.config), token, key, {
      fetched_at: before && before.fetched_at,
      last_attempt_at: before && before.last_attempt_at,
      seen_run: seenRun,
    });
    if (result.latest) state.latest = result.latest;
    if (result.status) state.status = result.status;
    state.queries[current.id] = query;
    state.focus = { routeId: current.id, key: key };
    render();
    if (result.error) {
      const box = document.getElementById("query-msg-" + current.id);
      if (box) box.textContent = result.error;
    }
    const target = document.getElementById(rowId(key));
    if (target) target.scrollIntoView({ block: "center" });
  } catch (err) {
    message.textContent = err.message;
  } finally {
    const liveButton = document.getElementById("fetch-" + current.id);
    if (liveButton) liveButton.disabled = false;
  }
}

function headlineFare(route) {
  const rows = routeRows(route).filter(function (row) {
    return typeof row.price === "number";
  });
  if (!rows.length) return null;
  return rows.slice().sort(function (a, b) {
    return a.price - b.price || String(a.depart).localeCompare(String(b.depart));
  })[0];
}

function fillChart(route) {
  const chartHost = document.getElementById("chart-" + route.id);
  if (!chartHost || chartHost.dataset.drawn === "1") return;
  chartHost.dataset.drawn = "1";
  const series = cheapestByDay(state.histories[route.id]);
  if (!series.length) {
    chartHost.append(el("p", { class: "chart-empty" }, ["History starts after the first successful scrape."]));
    return;
  }
  const canvas = el("canvas", { class: "spark", "aria-label": "Cheapest saved fare by day for " + route.label });
  chartHost.append(el("p", { class: "meta" }, ["Cheapest saved fare by day"]), canvas);
  requestAnimationFrame(function () {
    drawSparkline(canvas, series);
  });
}

function renderRoute(route) {
  const rows = routeRows(route);
  const fare = headlineFare(route);
  const open = !!state.open[route.id];
  const card = el("article", { class: "route" + (route.active === false ? " paused" : ""), id: "route-" + route.id });
  const dates = fare
    ? fare.origin + " → " + fare.dest + " · " + prettyDate(fare.depart) + (fare["return"] ? " – " + prettyDate(fare["return"]) : "")
    : "No fare saved yet";
  const updated = fare ? "Updated " + formatWhen(fare.fetched_at) : "Updated never";
  const toggle = el("button", { type: "button", class: "edit-toggle", "aria-expanded": open ? "true" : "false" }, [open ? "Close" : "Edit"]);
  const edit = el("div", { class: "route-edit", id: "edit-" + route.id });
  if (!open) edit.hidden = true;
  edit.append(
    el("p", { class: "how" }, [
      "Every day from the start date through the end date is checked, including weekends, unless you uncheck a weekday. Each departure is paired with each listed trip length. 7 through 15 means a 7-day trip, an 8-day trip, and so on up to 15 days. JFK runs in the morning, Newark in the afternoon, and Boston in the evening. Each of those runs refreshes " + ((state.config && state.config.settings && state.config.settings.max_requests_per_run) || 3000).toLocaleString("en-US") + " searches for that airport, then continues through the rest of that airport's dates the next time it runs.",
    ]),
    el("div", { class: "tools" }, [renderEditor(route), renderQuery(route)]),
    el("div", { class: "chart-wrap", id: "chart-" + route.id })
  );
  if (rows.length) edit.append(renderFareList(route, rows));
  toggle.addEventListener("click", function () {
    const willOpen = edit.hidden;
    edit.hidden = !willOpen;
    state.open[route.id] = willOpen;
    toggle.textContent = willOpen ? "Close" : "Edit";
    toggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
    if (willOpen) fillChart(route);
  });
  card.append(
    el("div", { class: "route-summary" }, [
      el("div", {}, [
        el("h2", {}, [route.label + (route.active === false ? " · paused" : "")]),
        el("p", { class: "fare-dates" }, [dates]),
        el("p", { class: "fare-updated" }, [updated + (fare && fare.stale ? " · last attempt failed" : "")]),
      ]),
      el("div", { class: "price-block" }, [
        el("strong", {}, [fare ? money(fare.price) : "—"]),
      ]),
      toggle,
    ]),
    edit
  );
  if (open) requestAnimationFrame(function () { fillChart(route); });
  return card;
}

function render() {
  renderHeader();
  const host = document.getElementById("routes");
  if (state.focus) {
    state.open[state.focus.routeId] = true;
    state.openFares[state.focus.key] = true;
  }
  const routes = state.config ? state.config.routes : (state.latest.routes || []);
  host.replaceChildren(el("div", { class: "route-list" }, routes.map(renderRoute)));
  routes.forEach(function (route) {
    if (route.origins && route.depart_window) paintPreview(route);
  });
  if (state.focus) {
    const node = document.getElementById(rowId(state.focus.key));
    state.focus = null;
    if (node) node.scrollIntoView({ block: "center" });
  }
}

async function boot() {
  syncTokenUi();
  try {
    const configPromise = loadConfig();
    const latestPromise = loadJson("data/latest.json");
    const statusPromise = loadJson("data/status.json");
    const loaded = await Promise.all([configPromise, latestPromise, statusPromise]);
    state.config = loaded[0];
    state.latest = loaded[1] || { routes: [], currency: "USD" };
    state.status = loaded[2];
    const ids = state.config.routes.map(function (route) { return route.id; });
    await Promise.all(
      ids.map(async function (id) {
        state.histories[id] = (await loadJson("data/history/" + id + ".json")) || { route_id: id, points: [] };
      })
    );
    render();
  } catch (err) {
    document.getElementById("run-line").textContent = "Could not load the dashboard.";
    setAction(err.message);
  }
}

document.getElementById("token-open").addEventListener("click", openTokenDialog);
document.getElementById("token-form").addEventListener("submit", function (event) {
  event.preventDefault();
  const value = document.getElementById("token-input").value.trim();
  if (value) localStorage.setItem(TOKEN_KEY, value);
  document.getElementById("token-dialog").close();
  syncTokenUi();
  setAction(value ? "Token saved in this browser." : "");
});
document.getElementById("token-clear").addEventListener("click", function () {
  localStorage.removeItem(TOKEN_KEY);
  document.getElementById("token-dialog").close();
  syncTokenUi();
  setAction("Token removed from this browser.");
});
document.getElementById("run-full").addEventListener("click", async function () {
  const token = getToken();
  if (!token) return openTokenDialog();
  const button = document.getElementById("run-full");
  button.disabled = true;
  try {
    await gh("/repos/" + repoOf(state.config) + "/actions/workflows/scrape.yml/dispatches", token, {
      method: "POST",
      body: JSON.stringify({ ref: "main", inputs: { mode: "full", route_id: "", origin: "", dest: "", depart: "", return_date: "" } }),
    });
    setAction("Full scrape started. Refresh this page after the Actions run finishes.");
  } catch (err) {
    setAction(err.message);
  } finally {
    button.disabled = false;
  }
});

boot();
