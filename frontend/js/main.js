/* Helpers shared by the attendance terminal and the admin pages. */
const T = (() => {
  const display = { timezone: "UTC", time_format: "12", date_format: "long" };
  const formatters = {};

  function setDisplay(values) {
    Object.assign(display, values);
    for (const key of Object.keys(formatters)) delete formatters[key];
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  class ApiFailure extends Error {
    constructor(message, code, status, data) {
      super(message);
      this.code = code;
      this.status = status;
      this.data = data;
    }
  }

  async function api(method, url, body, { timeout = 30000 } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    const options = { method, headers: { "X-Timora-Request": "1" }, signal: controller.signal, credentials: "same-origin" };
    if (body instanceof FormData) {
      options.body = body;
    } else if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(url, options);
    } catch (err) {
      throw new ApiFailure("Could not reach the server. Please check the connection and try again.", "NETWORK_ERROR", 0);
    } finally {
      clearTimeout(timer);
    }
    let data = null;
    try {
      data = await response.json();
    } catch (err) {
      throw new ApiFailure("The server sent an unexpected response.", "BAD_RESPONSE", response.status);
    }
    if (!response.ok || !data.success) {
      throw new ApiFailure(data.message || "Request failed.", data.code || "ERROR", response.status, data);
    }
    return data;
  }

  function fmt(key, options) {
    if (!formatters[key]) {
      formatters[key] = new Intl.DateTimeFormat("en-GB", { timeZone: display.timezone, ...options });
    }
    return formatters[key];
  }

  function parts(date, key, options) {
    const out = {};
    for (const part of fmt(key, options).formatToParts(date)) out[part.type] = part.value;
    return out;
  }

  /* {time:"11:45:32", ampm:"PM"} in the business timezone */
  function clockParts(date) {
    const h12 = display.time_format !== "24";
    const p = parts(date, "clock", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: h12 ? "h12" : "h23" });
    return { time: `${p.hour}:${p.minute}:${p.second}`, ampm: h12 ? (p.dayPeriod || "").toUpperCase() : "" };
  }

  function fmtClock(date) {
    const c = clockParts(date);
    return c.ampm ? `${c.time} ${c.ampm}` : c.time;
  }

  function hhmm(date) {
    const h12 = display.time_format !== "24";
    const p = parts(date, "hhmm", { hour: "2-digit", minute: "2-digit", hourCycle: h12 ? "h12" : "h23" });
    return h12 ? `${p.hour}:${p.minute} ${(p.dayPeriod || "").toUpperCase()}` : `${p.hour}:${p.minute}`;
  }

  function dateFromParts(y, m, d, name) {
    switch (display.date_format) {
      case "DD/MM/YYYY": return `${d}/${m}/${y}`;
      case "MM/DD/YYYY": return `${m}/${d}/${y}`;
      case "YYYY-MM-DD": return `${y}-${m}-${d}`;
      default: return name;
    }
  }

  /* Long date of an instant, in the business timezone */
  function fmtToday(date) {
    const p = parts(date, "ymd", { year: "numeric", month: "2-digit", day: "2-digit" });
    const long = fmt("long", { weekday: "long", day: "numeric", month: "long", year: "numeric" })
      .format(date).replace(/^(\w+) /, "$1, ");
    return dateFromParts(p.year, p.month, p.day, long);
  }

  /* 'YYYY-MM-DD' is a calendar date, so it is never shifted between timezones. */
  function fmtDate(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-");
    const asUtc = new Date(Date.UTC(+y, +m - 1, +d));
    const name = asUtc.toLocaleDateString("en-GB", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" });
    return dateFromParts(y, m, d, name);
  }

  function fmtDateTime(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    const p = parts(date, "ymd", { year: "numeric", month: "2-digit", day: "2-digit" });
    return `${fmtDate(`${p.year}-${p.month}-${p.day}`)}, ${hhmm(date)}`;
  }

  /* UTC ISO -> value for <input type="datetime-local"> in the business timezone */
  function toLocalInput(iso) {
    if (!iso) return "";
    const p = parts(new Date(iso), "input", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    });
    return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
  }

  function fmtDuration(seconds) {
    const minutes = Math.floor((seconds || 0) / 60);
    if (!minutes) return "-";
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
  }

  function fmtShiftTime(hm) {
    if (display.time_format === "24") return hm;
    const [h, m] = hm.split(":").map(Number);
    return `${String(h % 12 || 12).padStart(2, "0")}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
  }

  /* crypto.randomUUID needs HTTPS, and LAN deployments run on plain HTTP. */
  function requestId() {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  return { api, esc, setDisplay, display, clockParts, fmtClock, hhmm, fmtToday, fmtDate, fmtDateTime, toLocalInput, fmtDuration, fmtShiftTime, requestId, ApiFailure };
})();
