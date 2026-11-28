function quoteYaml(value) {
  return '"' + String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n") + '"';
}

function asDateString(value) {
  if (value == null || value === "") return "";
  if (Object.prototype.toString.call(value) === "[object Date]") {
    return value.toISOString().slice(0, 10);
  }
  return String(value).slice(0, 10);
}

function normalizeConfig(config) {
  const copy = JSON.parse(JSON.stringify(config));
  copy.routes = copy.routes || [];
  copy.routes.forEach(function (route) {
    route.depart_window = route.depart_window || {};
    route.depart_window.start = asDateString(route.depart_window.start);
    route.depart_window.end = asDateString(route.depart_window.end);
    route.extra_dates = (route.extra_dates || []).map(function (extra) {
      const next = {
        depart: asDateString(extra.depart),
      };
      const ret = extra["return"] || extra.return_date;
      if (ret) next["return"] = asDateString(ret);
      if (extra.trip_length_days) next.trip_length_days = extra.trip_length_days;
      if (extra.origin) next.origin = String(extra.origin).toUpperCase();
      if (extra.dest) next.dest = String(extra.dest).toUpperCase();
      return next;
    });
  });
  return copy;
}

function emitYaml(config) {
  const source = normalizeConfig(config);
  const settings = source.settings || {};
  const delay = settings.delay_seconds || [3, 7];
  const lines = [
    "settings:",
    "  currency: " + settings.currency,
    "  adults: " + settings.adults,
    "  cabin: " + settings.cabin,
    "  max_requests_per_run: " + settings.max_requests_per_run,
    "  delay_seconds: [" + delay[0] + ", " + delay[1] + "]",
    "  cooldown_minutes: " + (settings.cooldown_minutes == null ? 0 : settings.cooldown_minutes),
    "  dashboard_title: " + quoteYaml(settings.dashboard_title),
    "  source: " + settings.source,
    "  github_repo: " + settings.github_repo,
    "",
    "routes:",
  ];
  source.routes.forEach(function (route) {
    lines.push("  - id: " + route.id);
    lines.push("    label: " + quoteYaml(route.label));
    lines.push("    origins: [" + route.origins.join(", ") + "]");
    lines.push("    destinations: [" + route.destinations.join(", ") + "]");
    lines.push("    trip: " + route.trip);
    lines.push(
      "    depart_window: { start: " +
        route.depart_window.start +
        ", end: " +
        route.depart_window.end +
        " }"
    );
    if (route.depart_days && route.depart_days.length) {
      lines.push("    depart_days: [" + route.depart_days.join(", ") + "]");
    }
    if (route.trip_length_days && route.trip_length_days.length) {
      lines.push("    trip_length_days: [" + route.trip_length_days.join(", ") + "]");
    }
    if (route.max_stops != null) lines.push("    max_stops: " + route.max_stops);
    if (route.target_price != null) lines.push("    target_price: " + route.target_price);
    lines.push("    active: " + (route.active ? "true" : "false"));
    if (route.extra_dates.length) {
      lines.push("    extra_dates:");
      route.extra_dates.forEach(function (extra) {
        lines.push("      - depart: " + extra.depart);
        if (extra["return"]) lines.push("        return: " + extra["return"]);
        if (extra.trip_length_days) lines.push("        trip_length_days: " + extra.trip_length_days);
        if (extra.origin) lines.push("        origin: " + extra.origin);
        if (extra.dest) lines.push("        dest: " + extra.dest);
      });
    }
  });
  lines.push("");
  return lines.join("\n");
}

function searchKey(routeId, origin, dest, depart, ret) {
  return routeId + "|" + origin + "|" + dest + "|" + depart + "|" + (ret || "-");
}

function utcDate(iso) {
  const parts = iso.split("-").map(Number);
  return new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
}

function addDays(iso, days) {
  const date = utcDate(iso);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function daysBetween(start, end) {
  return Math.round((utcDate(end) - utcDate(start)) / 86400000);
}

function weekdayName(iso) {
  const names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const jsDay = utcDate(iso).getUTCDay();
  return names[jsDay === 0 ? 6 : jsDay - 1];
}

function nightsLabel(route) {
  const lengths = (route.trip_length_days || [])
    .map(Number)
    .filter(function (length) { return length > 0; })
    .sort(function (a, b) { return a - b; });
  if (!lengths.length) return "";
  if (lengths.length === 1) return lengths[0] + " nights";
  const contiguous = lengths.every(function (length, index) {
    return index === 0 || length === lengths[index - 1] + 1;
  });
  if (contiguous) return lengths[0] + "\u2013" + lengths[lengths.length - 1] + " nights";
  return lengths.join(", ") + " nights";
}

function inWindow(route, depart, ret) {
  const window = route.depart_window || {};
  if (!window.start || !window.end || depart < window.start || depart > window.end) return false;
  if (route.depart_days && route.depart_days.length && route.depart_days.indexOf(weekdayName(depart)) === -1) {
    return false;
  }
  if (route.trip === "round_trip") {
    const length = daysBetween(depart, ret);
    const lengths = route.trip_length_days || [];
    if (lengths.indexOf(length) === -1) return false;
  }
  return true;
}

globalThis.quoteYaml = quoteYaml;
globalThis.normalizeConfig = normalizeConfig;
globalThis.emitYaml = emitYaml;
globalThis.searchKey = searchKey;
globalThis.addDays = addDays;
globalThis.daysBetween = daysBetween;
globalThis.weekdayName = weekdayName;
globalThis.inWindow = inWindow;
globalThis.nightsLabel = nightsLabel;
