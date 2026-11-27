/* MedSight review console. Plain JavaScript with no outside code.
   Every value from the server is shown with textContent, never as HTML.
   Sign-in talks straight to Supabase Auth; everything else goes to /admin/kb. */
"use strict";

const CFG = window.REVIEW_CONFIG || {};
const SUPABASE = (CFG.supabaseUrl || "").replace(/\/$/, "");
const API = (CFG.apiBase || "").replace(/\/$/, "");
const STORE_KEY = "medsight-review-session";

const FLAGS = {
  otc_and_rx_same_product: ["bad", "OTC and Rx exist for this product"],
  export_only: ["bad", "Export only: can't be used"],
  expired: ["bad", "Expired"],
  expires_within_90_days: ["warn", "Expires within 90 days"],
  drhr_mismatch: ["warn", "DRHR number, not classified HR"],
  unsplit_strength: ["warn", "Strength unclear: read the portal"],
};
const STATE_BADGE = { pending: "warn", changes_requested: "bad", approved: "good" };
const LONG_FIELDS = new Set(["text", "spoken_description", "synonyms"]);
const FIELD_HINTS = {
  product_id: "lowercase letters, numbers and dashes",
  aliases: 'separate multiple names with "|", not ","',
  generic_names: 'separate ingredients with "|", not ","',
  strengths: 'separate with "|", not ",", in ingredient order',
  synonyms: 'separate multiple names with "|", not ","',
  reg_expiry: "YYYY-MM-DD",
  portal_checked_on: "YYYY-MM-DD",
  fda_reg_no: "e.g. DR-XY12345",
  source_url: "URL",
  spoken_description: "300 characters maximum; no dosing advice",
  source_ref: "source link or photo reference",
};
const CHOICES = {
  classification: ["", "OTC", "Rx", "HR"],
  pnf_listed: ["no", "yes"],
  section: ["indications", "warnings"],
  source_type: ["box", "insert", "pnf", "tfd"],
};

let session = loadSession();
let meta = null;
const queueView = { table: "", search: "", sort: "oldest", page: 1 };

// --- small helpers ---

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(props)) {
    if (name === "class") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else if (name in node) node[name] = value;
    else node.setAttribute(name, value);
  }
  for (const child of children) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

function show(...nodes) {
  document.getElementById("app").replaceChildren(...nodes);
  window.scrollTo(0, 0);
}

function say(message, kind = "ok") {
  const box = document.getElementById("status");
  box.textContent = message;
  box.className = kind;
  box.hidden = !message;
}

function clearSay() {
  say("");
}

function busy(button, label) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  button.setAttribute("aria-busy", "true");
  return () => {
    button.disabled = false;
    button.textContent = original;
    button.removeAttribute("aria-busy");
  };
}

function loading(label) {
  return el("div", { class: "loading", role: "status", "aria-live": "polite" },
    el("span", { class: "spinner", "aria-hidden": "true" }), label);
}

function loadSession() {
  try {
    return JSON.parse(sessionStorage.getItem(STORE_KEY)) || null;
  } catch {
    return null;
  }
}

function saveSession(next) {
  session = next;
  try {
    if (next) sessionStorage.setItem(STORE_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(STORE_KEY);
  } catch {
    // private window: the session just lasts as long as this page
  }
}

function errorText(body, status) {
  const detail = body && (body.detail || body.msg || body.error_description || body.message);
  if (Array.isArray(detail)) return detail.map((d) => (d && d.msg) || String(d)).join(" ");
  return detail ? String(detail) : `Something went wrong (${status}).`;
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

// --- Supabase Auth ---

async function auth(path, { method = "GET", body, token } = {}) {
  const headers = { apikey: CFG.supabaseAnonKey || "" };
  if (body) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${SUPABASE}/auth/v1${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await readJson(response);
  if (!response.ok) throw new Error(errorText(data, response.status));
  return data;
}

function keepTokens(data) {
  saveSession({ access: data.access_token, refresh: data.refresh_token });
}

async function refreshTokens() {
  if (!session || !session.refresh) return false;
  try {
    keepTokens(await auth("/token?grant_type=refresh_token", {
      method: "POST",
      body: { refresh_token: session.refresh },
    }));
    return true;
  } catch {
    return false;
  }
}

// --- the review API ---

async function api(path, { method = "GET", json, form } = {}, retried = false) {
  const headers = { Authorization: `Bearer ${session ? session.access : ""}` };
  if (json) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API}${path}`, {
    method,
    headers,
    body: json ? JSON.stringify(json) : form,
  });
  if (response.status === 401 && !retried && await refreshTokens()) {
    return api(path, { method, json, form }, true);
  }
  const data = await readJson(response);
  if (response.status === 401) {
    signedOut("Please sign in again.");
    throw new Error("Please sign in again.");
  }
  if (!response.ok) throw new Error(errorText(data, response.status));
  return data;
}

function kb(table, key = "", suffix = "") {
  const keyPart = key ? `/${encodeURIComponent(key)}` : "";
  return `/admin/kb/${encodeURIComponent(table)}${keyPart}${suffix}`;
}

function rowLink(table, key) {
  return `#/row/${encodeURIComponent(table)}/${encodeURIComponent(key)}`;
}

// --- signing in ---

function eyeIcon(visible) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "20");
  svg.setAttribute("height", "20");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const outline = document.createElementNS(ns, "path");
  outline.setAttribute("d", "M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z");
  const pupil = document.createElementNS(ns, "circle");
  pupil.setAttribute("cx", "12");
  pupil.setAttribute("cy", "12");
  pupil.setAttribute("r", "2.5");
  svg.append(outline, pupil);
  if (visible) {
    const slash = document.createElementNS(ns, "path");
    slash.setAttribute("d", "M4 20 20 4");
    svg.append(slash);
  }
  return svg;
}

function signedOut(message) {
  saveSession(null);
  meta = null;
  document.getElementById("top").hidden = true;
  showSignIn(message);
}

function showSignIn(message) {
  const email = el("input", { type: "email", autocomplete: "username", required: true });
  const password = el("input", { id: "review-password", type: "password", autocomplete: "current-password", required: true });
  const visibility = el("button", {
    type: "button",
    class: "quiet password-toggle",
    "aria-controls": "review-password",
    "aria-pressed": "false",
    "aria-label": "Show password",
    title: "Show password",
    onclick: () => {
      const showing = password.type === "password";
      password.type = showing ? "text" : "password";
      const label = showing ? "Hide password" : "Show password";
      visibility.replaceChildren(eyeIcon(showing));
      visibility.setAttribute("aria-label", label);
      visibility.title = label;
      visibility.setAttribute("aria-pressed", String(showing));
    },
  }, eyeIcon(false));
  const button = el("button", { type: "submit" }, "Sign in");
  const form = el("form", {
    onsubmit: async (event) => {
      event.preventDefault();
      const done = busy(button, "Signing in…");
      clearSay();
      try {
        keepTokens(await auth("/token?grant_type=password", {
          method: "POST",
          body: { email: email.value.trim(), password: password.value },
        }));
        if (CFG.requireMfa !== false) {   // strict unless the server says otherwise
          await secondFactor();
        } else {
          clearSay();
          await start();
        }
      } catch (error) {
        say(error.message, "error");
        done();
      }
    },
  },
  el("label", {}, "Email", email),
  el("label", { htmlFor: "review-password" }, "Password"),
  el("div", { class: "password-field" }, password, visibility),
  button);
  if (message) say(message, "error");
  show(el("section", { class: "card narrow" }, el("h1", {}, "MedSight Review"), form));
  email.focus();
}

async function secondFactor() {
  const user = await auth("/user", { token: session.access });
  const factors = (user.factors || []).filter((f) => f.factor_type === "totp");
  const verified = factors.find((f) => f.status === "verified");
  if (verified) {
    showCodeScreen(verified.id, "Enter the 6-digit code from your authenticator app.");
    return;
  }
  for (const stale of factors) {   // an earlier setup that was never finished
    await auth(`/factors/${encodeURIComponent(stale.id)}`, { method: "DELETE", token: session.access });
  }
  const enrolled = await auth("/factors", {
    method: "POST",
    token: session.access,
    body: { factor_type: "totp", friendly_name: "MedSight review" },
  });
  showCodeScreen(enrolled.id, "Add this account to your authenticator app, then enter its 6-digit code.", enrolled.totp);
}

function showCodeScreen(factorId, intro, totp) {
  const code = el("input", { type: "text", inputMode: "numeric", autocomplete: "one-time-code", maxLength: 6, required: true });
  const button = el("button", { type: "submit" }, "Continue");
  const form = el("form", {
    onsubmit: async (event) => {
      event.preventDefault();
      const done = busy(button, "Checking code…");
      clearSay();
      try {
        const path = `/factors/${encodeURIComponent(factorId)}`;
        const challenge = await auth(`${path}/challenge`, { method: "POST", token: session.access });
        keepTokens(await auth(`${path}/verify`, {
          method: "POST",
          token: session.access,
          body: { challenge_id: challenge.id, code: code.value.trim() },
        }));
        clearSay();
        await start();
      } catch (error) {
        say(error.message, "error");
        done();
      }
    },
  },
  el("label", {}, "Code", code),
  button);

  const setup = [];
  if (totp) {
    const qr = String(totp.qr_code || "");
    const src = qr.startsWith("data:") ? qr : `data:image/svg+xml;utf-8,${encodeURIComponent(qr)}`;
    setup.push(el("img", { class: "qr", src, alt: "QR code for your authenticator app" }));
    setup.push(el("p", {}, "Or type this key: ", el("span", { class: "secret" }, String(totp.secret || ""))));
  }
  show(el("section", { class: "card narrow" }, el("h1", {}, "Two-factor code"), el("p", {}, intro), ...setup, form));
  code.focus();
}

// --- the queue and "my rows" ---

function stateBadge(row) {
  return el("span", { class: `badge ${STATE_BADGE[row.review_state] || ""}` }, row.review_state.replace("_", " "));
}

function rowsTable(rows, emptyText) {
  if (!rows.length) return el("p", { class: "muted" }, emptyText);
  const body = rows.map((row) => el("tr", {},
    el("td", {}, el("a", { href: rowLink(row.table_name, row.key) }, row.key)),
    el("td", {}, row.table_name),
    el("td", {}, row.label),
    el("td", {}, stateBadge(row)),
    el("td", {}, row.entered_by)));
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...["Row", "Table", "Name", "State", "Entered by"].map((h) => el("th", { scope: "col" }, h)))),
    el("tbody", {}, ...body)));
}

async function showQueue() {
  const table = el("select", {},
    el("option", { value: "" }, "All tables"),
    ...["products", "ingredients", "passages"].map((t) => el("option", { value: t }, t)));
  table.value = queueView.table;
  const search = el("input", { type: "text", placeholder: "Brand or ID", value: queueView.search });
  const sort = el("select", {},
    ...[["oldest", "Oldest added first"], ["newest", "Newest added first"],
      ["name_asc", "Name A–Z"], ["name_desc", "Name Z–A"],
      ["updated", "Recently updated"]].map(([value, label]) => el("option", { value }, label)));
  sort.value = queueView.sort;
  const results = el("div", {});
  const pagination = el("div", { class: "pagination" });
  const stats = el("section", { class: "stats-grid", "aria-label": "Review totals" },
    loading("Loading review totals…"));
  const feedback = el("div", { class: "queue-feedback", role: "status", "aria-live": "polite" });
  let request = 0;

  async function load() {
    const current = ++request;
    feedback.replaceChildren(loading("Updating results…"));
    results.setAttribute("aria-busy", "true");
    try {
      const params = new URLSearchParams();
      if (queueView.table) params.set("table", queueView.table);
      if (queueView.search) params.set("q", queueView.search);
      params.set("sort", queueView.sort);
      params.set("page", String(queueView.page));
      const data = await api(`/admin/kb/queue-page?${params}`);
      if (current !== request) return;
      if (data.total && queueView.page > Math.ceil(data.total / 20)) {
        queueView.page = Math.ceil(data.total / 20);
        return load();
      }
      const empty = queueView.table || queueView.search
        ? "No items match your filters." : "You're all caught up. Nothing is waiting for review.";
      results.replaceChildren(queueRows(data.items, empty));
      const start = data.total ? (queueView.page - 1) * 20 + 1 : 0;
      const end = Math.min(queueView.page * 20, data.total);
      const previous = el("button", { type: "button", class: "quiet", disabled: queueView.page === 1,
        onclick: () => { queueView.page--; load(); } }, "Previous");
      const next = el("button", { type: "button", class: "quiet", disabled: end >= data.total,
        onclick: () => { queueView.page++; load(); } }, "Next");
      pagination.replaceChildren(el("span", {}, `${start}–${end} of ${data.total} · Page ${queueView.page} of ${Math.max(1, Math.ceil(data.total / 20))}`),
        el("div", { class: "row-actions" }, previous, next));
      feedback.replaceChildren();
    } catch (error) {
      if (current !== request) return;
      feedback.replaceChildren(el("span", { class: "error-text" }, error.message), " ",
        el("button", { type: "button", class: "quiet", onclick: load }, "Retry"));
    } finally {
      if (current === request) results.removeAttribute("aria-busy");
    }
  }

  const clear = el("button", { type: "button", class: "quiet", hidden: !queueView.table && !queueView.search,
    onclick: () => {
      table.value = "";
      search.value = "";
      queueView.table = "";
      queueView.search = "";
      queueView.page = 1;
      clear.hidden = true;
      load();
    } }, "Clear filters");
  const form = el("form", {
    class: "filters",
    onsubmit: (event) => {
      event.preventDefault();
      queueView.table = table.value;
      queueView.search = search.value.trim();
      queueView.sort = sort.value;
      queueView.page = 1;
      clear.hidden = !queueView.table && !queueView.search;
      load();
    },
  },
  el("label", {}, "Table", table),
  el("label", {}, "Sort by", sort),
  el("label", {}, "Search", search),
  el("button", { type: "submit" }, "Apply filters"),
  clear);

  results.replaceChildren(loading("Loading review queue…"));
  show(el("h1", {}, "Waiting for review"), stats, form, feedback,
    el("section", { class: "card" }, results), pagination);
  await load();
  try {
    const counts = await api("/admin/review/stats");
    stats.replaceChildren(...["products", "ingredients", "passages"].map((name) =>
      el("article", { class: "stat-card" },
        el("h2", {}, name[0].toUpperCase() + name.slice(1)),
        el("p", {}, el("strong", {}, String(counts[name].pending)), " pending"),
        el("p", {}, el("strong", {}, String(counts[name].approved)), " approved"))),
    el("article", { class: "stat-card" },
      el("h2", {}, "FDA candidates"),
      el("p", {}, el("strong", {}, String(counts.fda_candidates)), " candidates")));
  } catch (error) {
    stats.replaceChildren(el("p", { class: "muted" }, `Review totals unavailable: ${error.message}`));
  }
}

function queueRows(rows, emptyText) {
  if (!rows.length) return el("p", { class: "muted" }, emptyText);
  return el("div", { class: "queue-list" }, ...rows.map((row) =>
    el("article", { class: "queue-item" },
      el("div", {},
        el("a", { class: "queue-link", href: rowLink(row.table_name, row.key) }, row.label || row.key),
        el("p", { class: "muted" }, `${row.table_name} · Added ${new Date(row.created_at).toLocaleDateString()}`)),
      stateBadge(row))));
}

async function showMine() {
  const rows = await api("/admin/kb/mine");
  show(el("h1", {}, "My rows"),
    el("p", { class: "muted" }, "Rows you entered or last edited. Someone else has to check these."),
    el("section", { class: "card" }, rowsTable(rows, "You haven't entered or edited any rows yet.")));
}

// --- one row ---

function valuesTable(row, fields) {
  return el("div", { class: "table-wrap" }, el("table", { class: "values" }, el("tbody", {}, ...fields.map((field) => {
    const value = row[field];
    return el("tr", {},
      el("th", { scope: "row" }, field),
      value === "" || value === null || value === undefined
        ? el("td", { class: "empty-value" }, "empty")
        : el("td", {}, String(value)));
  }))));
}

function previewCard(text) {
  if (!text) return null;
  const play = el("button", {
    type: "button",
    class: "quiet",
    onclick: () => {
      if (!window.speechSynthesis) {
        say("This browser can't read text aloud.", "error");
        return;
      }
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
    },
  }, "Play");
  return el("section", { class: "card" }, el("h2", {}, "What the user will hear"), el("p", { class: "preview" }, text), play);
}

function historyCard(history) {
  if (!history.length) return null;
  return el("section", { class: "card" }, el("h2", {}, "Review history"), ...history.map((item) => el("p", {},
    el("strong", {}, `${item.decision === "approve" ? "Approved" : "Changes requested"} by ${item.reviewer}`),
    el("span", { class: "muted" }, ` · ${item.created_at}`),
    item.note ? el("span", {}, ` · ${item.note}`) : null)));
}

function evidenceCard(table, key, evidence, reload) {
  const kind = el("select", {},
    el("option", { value: "box_photo" }, "Box photo"),
    el("option", { value: "insert" }, "Package insert"),
    el("option", { value: "portal_screenshot" }, "FDA portal screenshot"));
  const file = el("input", { type: "file", accept: "image/jpeg,image/png", required: true });
  const button = el("button", { type: "submit" }, "Upload");
  const form = el("form", {
    onsubmit: async (event) => {
      event.preventDefault();
      const done = busy(button, "Uploading…");
      try {
        const data = new FormData();
        data.append("kind", kind.value);
        data.append("file", file.files[0]);
        await api(kb(table, key, "/evidence"), { method: "POST", form: data });
        say("Evidence uploaded.");
        await reload();
      } catch (error) {
        say(error.message, "error");
        done();
      }
    },
  },
  el("label", {}, "Type", kind),
  el("label", {}, "JPEG or PNG, 5 MB at most", file),
  button);

  const items = evidence.map((item) => el("figure", {},
    item.url
      ? el("a", { href: item.url, target: "_blank", rel: "noopener" }, el("img", { src: item.url, alt: item.kind.replace("_", " ") }))
      : el("p", { class: "muted" }, "Link unavailable"),
    el("figcaption", {}, `${item.kind.replace("_", " ")} · ${item.uploaded_by}`)));
  return el("section", { class: "card" }, el("h2", {}, "Evidence"),
    items.length ? el("div", { class: "evidence" }, ...items) : el("p", { class: "muted" }, "No evidence uploaded yet."),
    form);
}

function candidateNode(key, candidate, reload) {
  const badges = candidate.flags.map((flag) => {
    const [kind, label] = FLAGS[flag] || ["warn", flag];
    return el("span", { class: `badge ${kind}` }, label);
  });
  const pick = el("button", {
    type: "button",
    class: "quiet",
    disabled: candidate.export_only,
    onclick: async () => {
      const done = busy(pick, "Copying…");
      try {
        await api(kb("products", key, "/fda-pick"), { method: "POST", json: { candidate_id: candidate.id } });
        say("FDA values copied. This counts as your edit, so someone else must approve the row.");
        await reload();
      } catch (error) {
        say(error.message, "error");
        done();
      }
    },
  }, "Use these values");
  const details = [candidate.strength, candidate.dosage_form].filter(Boolean).join(" ")
    || "Strength and form unclear";
  return el("div", { class: "candidate" },
    el("strong", {}, candidate.fda_reg_no),
    el("div", {}, `${candidate.brand_name} · ${details}`),
    el("div", { class: "muted" }, `${candidate.classification || "?"} · expires ${candidate.reg_expiry || "?"} · ${candidate.manufacturer || "no manufacturer"}`),
    el("div", {}, ...badges),
    el("div", { class: "row-actions" }, pick));
}

async function fdaCard(key, reload) {
  const candidates = await api(kb("products", key, "/fda-candidates"));
  const link = el("a", { href: "https://verification.fda.gov.ph", target: "_blank", rel: "noopener" }, "FDA portal");
  return el("section", { class: "card" }, el("h2", {}, "FDA candidates"),
    el("p", { class: "muted" }, "Match the number printed on the box, then confirm it on the ", link, "."),
    ...(candidates.length
      ? candidates.map((c) => candidateNode(key, c, reload))
      : [el("p", { class: "muted" }, "No candidates for this brand.")]));
}

function decisionCard(table, key, data, reload) {
  if (data.row.approved) {
    return el("section", { class: "card" }, el("h2", {}, "Decision"), el("p", {}, `Approved by ${data.row.checked_by}.`));
  }
  if (data.own_row) {
    return el("section", { class: "card" }, el("h2", {}, "Decision"),
      el("p", { class: "notice" }, "You entered or last edited this row, so someone else must check it."));
  }
  const approve = el("button", { type: "button", disabled: true }, "Approve");
  const boxes = data.checklist.map((item) => ({
    key: item.key,
    input: el("input", { type: "checkbox", onchange: () => { approve.disabled = !boxes.every((b) => b.input.checked); } }),
    label: item.label,
  }));
  approve.addEventListener("click", async () => {
    const done = busy(approve, "Saving review…");
    try {
      const checklist = Object.fromEntries(boxes.map((b) => [b.key, b.input.checked]));
      await api(kb(table, key, "/approve"), { method: "POST", json: { checklist, version: data.version } });
      say("Approved. The app will use this row within about a minute.");
      location.hash = "#/queue";
    } catch (error) {
      say(error.message, "error");
      done();
      approve.disabled = !boxes.every((b) => b.input.checked);
    }
  });

  const note = el("textarea", { required: true });
  const send = el("button", { type: "submit", class: "quiet" }, "Request changes");
  const changes = el("form", {
    onsubmit: async (event) => {
      event.preventDefault();
      const done = busy(send, "Saving review…");
      try {
        await api(kb(table, key, "/request-changes"), { method: "POST", json: { note: note.value } });
        say("Changes requested.");
        await reload();
      } catch (error) {
        say(error.message, "error");
        done();
      }
    },
  },
  el("label", {}, "What needs changing?", note),
  send);

  return el("section", { class: "card" }, el("h2", {}, "Decision"),
    ...boxes.map((b) => el("label", { class: "check" }, b.input, el("span", {}, b.label))),
    el("div", { class: "row-actions" }, approve),
    el("hr", {}),
    changes);
}

async function showRow(table, key) {
  const reload = () => showRow(table, key);
  const data = await api(kb(table, key));
  if (!meta) meta = await api("/admin/kb/meta");
  const fields = [meta.tables[table].key, ...meta.tables[table].fields, "entered_by", "last_edited_by", "checked_by"];
  const left = [
    el("section", { class: "card" }, el("h2", {}, "Values"), valuesTable(data.row, fields),
      data.row.review_note ? el("p", { class: "notice" }, `Reviewer's note: ${data.row.review_note}`) : null,
      el("div", { class: "row-actions" },
        table === "products"
          ? el("button", { type: "button",
            onclick: () => { location.hash = `#/edit/${encodeURIComponent(table)}/${encodeURIComponent(key)}`; } }, "Edit this row")
          : el("a", { href: `#/edit/${encodeURIComponent(table)}/${encodeURIComponent(key)}` }, "Edit this row"),
        deleteButton(table, key, data.row))),
    decisionCard(table, key, data, reload),
    historyCard(data.history),
  ];
  const right = [
    evidenceCard(table, key, data.evidence, reload),
    table === "products" ? await fdaCard(key, reload) : null,
    previewCard(data.spoken_preview),
  ];
  show(el("h1", {}, key),
    el("p", {}, el("span", { class: "badge" }, table), stateBadge(data.row)),
    el("div", { class: "columns" }, el("div", {}, ...left.filter(Boolean)), el("div", {}, ...right.filter(Boolean))));
}

function deleteButton(table, key, row) {
  if (row.approved || !meta || row.entered_by.trim().toLowerCase() !== meta.reviewer.trim().toLowerCase()) return null;
  return el("button", {
    type: "button",
    class: "danger",
    onclick: async () => {
      if (!window.confirm(`Delete ${key}? This can't be undone.`)) return;
      try {
        await api(kb(table, key), { method: "DELETE" });
        say("Row deleted.");
        location.hash = "#/mine";
      } catch (error) {
        say(error.message, "error");
      }
    },
  }, "Delete");
}

// --- adding and editing ---

function fieldInput(field, value) {
  if (CHOICES[field]) {
    const select = el("select", {}, ...CHOICES[field].map((option) => el("option", { value: option }, option || "(not set)")));
    select.value = value;
    return select;
  }
  if (LONG_FIELDS.has(field)) return el("textarea", { value });
  return el("input", { type: "text", value });
}

function rowForm(table, fields, row, onSubmit, submitLabel) {
  const inputs = fields.map((field) => ({ field, input: fieldInput(field, row[field] || "") }));
  const button = el("button", { type: "submit" }, submitLabel);
  return el("form", {
    onsubmit: async (event) => {
      event.preventDefault();
      const done = busy(button, "Saving…");
      try {
        await onSubmit(Object.fromEntries(inputs.map(({ field, input }) => [field, input.value])));
      } catch (error) {
        say(error.message, "error");
        done();
      }
    },
  },
  ...inputs.map(({ field, input }) =>
    el("label", {}, FIELD_HINTS[field] ? `${field} (${FIELD_HINTS[field]})` : field, input)),
  button);
}

async function showAdd(table) {
  if (!meta) meta = await api("/admin/kb/meta");
  if (!meta.tables[table]) throw new Error("Unknown table.");
  const defn = meta.tables[table];
  const picker = el("select", {
    onchange: () => { location.hash = `#/add/${picker.value}`; },
  }, ...Object.keys(meta.tables).map((t) => el("option", { value: t }, t)));
  picker.value = table;
  const form = rowForm(table, [defn.key, ...defn.fields], table === "ingredients" ? { pnf_listed: "no" } : {}, async (values) => {
    const added = await api(kb(table), { method: "POST", json: { values } });
    say("Row added. It's now waiting for someone else to check it.");
    location.hash = rowLink(table, added.key);
  }, "Add row");
  show(el("h1", {}, "Add a row"), el("section", { class: "card" }, el("label", {}, "Table", picker), form));
}

async function showEdit(table, key) {
  if (!meta) meta = await api("/admin/kb/meta");
  const data = await api(kb(table, key));
  const original = data.row;
  const form = rowForm(table, meta.tables[table].fields, original, async (values) => {
    const changed = Object.fromEntries(Object.entries(values).filter(([f, v]) => v !== (original[f] || "")));
    if (!Object.keys(changed).length) {
      say("Nothing changed.");
      location.hash = rowLink(table, key);
      return;
    }
    await api(kb(table, key), { method: "PATCH", json: { values: changed } });
    say("Saved. The row is back in the queue for someone else to check.");
    location.hash = rowLink(table, key);
  }, "Save changes");
  show(el("h1", {}, `Edit ${key}`),
    data.row.approved ? el("p", { class: "notice" }, "This row is approved. Saving a change takes it out of the app until someone approves it again.") : null,
    el("section", { class: "card" }, form));
}

// --- web answers ---

function isTfd(url) {
  return /^https:\/\/(www\.)?thefilipinodoctor\.com\/\S+$/.test(url || "");
}

function sendForm(answer) {
  const source = answer.sources[0] || "";
  const tfd = isTfd(source);
  // Only a TheFilipinoDoctor page can be the passage's source, so only its text is prefilled.
  const inputs = {
    brand_name: el("input", { type: "text", value: answer.brand || "" }),
    generic_names: el("input", { type: "text", value: answer.generics.join("|") }),
    strengths: el("input", { type: "text", value: answer.strengths.join("|") }),
    dosage_form: el("input", { type: "text", value: "" }),
    product_id: el("input", { type: "text", value: "", placeholder: "Leave empty to make one from the above" }),
    source_type: fieldInput("source_type", tfd ? "tfd" : "box"),
    source_ref: el("input", { type: "text", value: tfd ? source : "", placeholder: "Photo name, or the TheFilipinoDoctor link" }),
    passage_text: el("textarea", { value: tfd ? answer.page_text : "" }),
  };
  const labels = { brand_name: "Brand", generic_names: 'Ingredients (separate with "|", not ",")', strengths: 'Strengths, same order (separate with "|", not ",")',
    dosage_form: "Dosage form", product_id: "Product ID", source_type: "What backs this", source_ref: "Source reference", passage_text: "Indications text, word for word from that source" };
  const button = el("button", { type: "submit" }, "Create pending rows");
  return el("form", {
    onsubmit: async (event) => {
      event.preventDefault();
      const done = busy(button, "Creating rows…");
      try {
        const json = Object.fromEntries(Object.entries(inputs).map(([field, input]) => [field, input.value]));
        const sent = await api(`/admin/web-answers/${encodeURIComponent(answer.cache_key)}/send`, { method: "POST", json });
        say(`Created pending rows for ${sent.product_id}. Someone else must check them.`);
        location.hash = rowLink("products", sent.product_id);
      } catch (error) {
        say(error.message, "error");
        done();
      }
    },
  },
  el("p", { class: "notice" }, "A web page alone can't back a reviewed medicine. You need the box, the insert or a TheFilipinoDoctor page. FDA values are added afterwards from the portal."),
  ...Object.entries(inputs).map(([field, input]) => el("label", {}, labels[field], input)),
  button);
}

function webAnswerCard(answer, reload) {
  const sources = answer.sources.filter((url) => url.startsWith("https://"))
    .map((url) => el("p", {}, el("a", { href: url, target: "_blank", rel: "noopener" }, url)));
  const name = [answer.brand, answer.generics.map((g, i) => `${g} ${answer.strengths[i] || ""}`.trim()).join(" + ")].filter(Boolean).join(", ");
  const holder = el("div", {});
  const reject = el("button", {
    type: "button",
    class: "quiet",
    onclick: async () => {
      if (!confirm("Stop speaking this answer? The next scan searches again, so it can come back if the page says the same thing.")) return;
      reject.disabled = true;
      try {
        await api(`/admin/web-answers/${encodeURIComponent(answer.cache_key)}/reject`, { method: "POST" });
        say("Rejected. It's no longer spoken.");
        await reload();
      } catch (error) {
        say(error.message, "error");
        reject.disabled = false;
      }
    },
  }, "Reject");
  const send = el("button", {
    type: "button",
    onclick: () => {
      holder.replaceChildren(sendForm(answer));
      send.disabled = true;
    },
  }, "Send to kb");
  const page = answer.page_text
    ? el("details", {}, el("summary", {}, "Page text this was based on"), el("p", {}, answer.page_text))
    : el("p", { class: "muted" }, "The page text wasn't saved for this answer.");
  return el("section", { class: "card" },
    el("h2", {}, name || "Unnamed product"),
    el("p", {},
      el("span", { class: "badge" }, answer.status.replace("_", " ")),
      answer.prescription ? el("span", { class: "badge warn" }, "Page says prescription") : null),
    el("p", {}, el("strong", {}, "Users hear: "), answer.spoken),
    ...sources,
    el("p", { class: "muted" }, `Saved ${answer.cached_at}`),
    page,
    el("div", { class: "row-actions" }, send, reject),
    holder);
}

async function showWebAnswers() {
  const answers = await api("/admin/web-answers");
  const reload = () => showWebAnswers();
  show(el("h1", {}, "Web answers"),
    el("p", { class: "muted" }, "Answers from the allowed sites. Users hear them with a warning while they wait here."),
    ...(answers.length
      ? answers.map((answer) => webAnswerCard(answer, reload))
      : [el("section", { class: "card" }, el("p", { class: "muted" }, "No web answers are waiting."))]));
}

// --- routing ---

async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  const active = ["mine", "web", "add"].includes(parts[0]) ? parts[0] : "queue";
  for (const link of document.querySelectorAll("nav .nav-button")) {
    if (link.getAttribute("href").startsWith(`#/${active}`)) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  try {
    if (parts[0] && parts[0] !== "queue") {
      show(el("h1", {}, "MedSight Review"), el("section", { class: "card" }, loading("Loading screen…")));
    }
    if (parts[0] === "row" && parts[2]) await showRow(parts[1], parts[2]);
    else if (parts[0] === "edit" && parts[2]) await showEdit(parts[1], parts[2]);
    else if (parts[0] === "add") await showAdd(parts[1] || "products");
    else if (parts[0] === "mine") await showMine();
    else if (parts[0] === "web") await showWebAnswers();
    else await showQueue();
  } catch (error) {
    if (session) say(error.message, "error");
  }
}

async function start() {
  try {
    meta = await api("/admin/kb/meta");
  } catch (error) {
    if (session) {
      saveSession(null);
      showSignIn(error.message);
    }
    return;
  }
  document.getElementById("who-name").textContent = meta.reviewer;
  document.getElementById("top").hidden = false;
  await route();
}

window.addEventListener("hashchange", () => {
  if (meta) route();
});

document.getElementById("sign-out").addEventListener("click", async () => {
  try {
    await auth("/logout", { method: "POST", token: session && session.access });
  } catch {
    // the local sign-out below still happens
  }
  clearSay();
  signedOut();
});

if (!SUPABASE) say("The console isn't configured: SUPABASE_URL is missing.", "error");
else if (session) start();
else showSignIn();
