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
const CHOICES = {
  classification: ["", "OTC", "Rx", "HR"],
  pnf_listed: ["no", "yes"],
  section: ["indications", "warnings"],
  source_type: ["box", "insert", "pnf", "tfd"],
};

let session = loadSession();
let meta = null;

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

function signedOut(message) {
  saveSession(null);
  meta = null;
  document.getElementById("top").hidden = true;
  showSignIn(message);
}

function showSignIn(message) {
  const email = el("input", { type: "email", autocomplete: "username", required: true });
  const password = el("input", { type: "password", autocomplete: "current-password", required: true });
  const button = el("button", { type: "submit" }, "Sign in");
  const form = el("form", {
    onsubmit: async (event) => {
      event.preventDefault();
      button.disabled = true;
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
        button.disabled = false;
      }
    },
  },
  el("label", {}, "Email", email),
  el("label", {}, "Password", password),
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
      button.disabled = true;
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
        button.disabled = false;
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
  const search = el("input", { type: "text", placeholder: "Brand or ID" });
  const results = el("div", {});
  const stats = el("p", { class: "stats" });

  async function load() {
    try {
      const params = new URLSearchParams();
      if (table.value) params.set("table", table.value);
      if (search.value.trim()) params.set("q", search.value.trim());
      results.replaceChildren(rowsTable(await api(`/admin/kb/queue?${params}`), "Nothing is waiting for you to review."));
    } catch (error) {
      say(error.message, "error");
    }
  }

  const form = el("form", {
    class: "filters",
    onsubmit: (event) => {
      event.preventDefault();
      load();
    },
  },
  el("label", {}, "Table", table),
  el("label", {}, "Search", search),
  el("button", { type: "submit" }, "Filter"));

  show(el("h1", {}, "Waiting for review"), stats, form, el("section", { class: "card" }, results));
  await load();
  try {
    const counts = await api("/admin/review/stats");
    const line = ["products", "ingredients", "passages"]
      .map((t) => `${t}: ${counts[t].pending} pending, ${counts[t].approved} approved`).join(" · ");
    stats.textContent = `${line} · FDA candidates: ${counts.fda_candidates}`;
  } catch {
    stats.textContent = "";
  }
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
      button.disabled = true;
      try {
        const data = new FormData();
        data.append("kind", kind.value);
        data.append("file", file.files[0]);
        await api(kb(table, key, "/evidence"), { method: "POST", form: data });
        say("Evidence uploaded.");
        await reload();
      } catch (error) {
        say(error.message, "error");
        button.disabled = false;
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
      pick.disabled = true;
      try {
        await api(kb("products", key, "/fda-pick"), { method: "POST", json: { candidate_id: candidate.id } });
        say("FDA values copied. This counts as your edit, so someone else must approve the row.");
        await reload();
      } catch (error) {
        say(error.message, "error");
        pick.disabled = false;
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
    approve.disabled = true;
    try {
      const checklist = Object.fromEntries(boxes.map((b) => [b.key, b.input.checked]));
      await api(kb(table, key, "/approve"), { method: "POST", json: { checklist, version: data.version } });
      say("Approved. The app will use this row within about a minute.");
      location.hash = "#/queue";
    } catch (error) {
      say(error.message, "error");
      approve.disabled = !boxes.every((b) => b.input.checked);
    }
  });

  const note = el("textarea", { required: true });
  const send = el("button", { type: "submit", class: "quiet" }, "Request changes");
  const changes = el("form", {
    onsubmit: async (event) => {
      event.preventDefault();
      send.disabled = true;
      try {
        await api(kb(table, key, "/request-changes"), { method: "POST", json: { note: note.value } });
        say("Changes requested.");
        await reload();
      } catch (error) {
        say(error.message, "error");
        send.disabled = false;
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
        el("a", { href: `#/edit/${encodeURIComponent(table)}/${encodeURIComponent(key)}` }, "Edit this row"),
        deleteButton(table, key, data.row))),
    previewCard(data.spoken_preview),
    historyCard(data.history),
  ];
  const right = [
    evidenceCard(table, key, data.evidence, reload),
    table === "products" ? await fdaCard(key, reload) : null,
    decisionCard(table, key, data, reload),
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
      button.disabled = true;
      try {
        await onSubmit(Object.fromEntries(inputs.map(({ field, input }) => [field, input.value])));
      } catch (error) {
        say(error.message, "error");
        button.disabled = false;
      }
    },
  },
  ...inputs.map(({ field, input }) => el("label", {}, field, input)),
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

// --- routing ---

async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  try {
    if (parts[0] === "row" && parts[2]) await showRow(parts[1], parts[2]);
    else if (parts[0] === "edit" && parts[2]) await showEdit(parts[1], parts[2]);
    else if (parts[0] === "add") await showAdd(parts[1] || "products");
    else if (parts[0] === "mine") await showMine();
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
