// Vercel build for the hosted review page: copies the page into dist/ and
// writes dist/config.js from the project's environment variables.
//
//   REVIEW_SUPABASE_URL       https://<project-ref>.supabase.co
//   REVIEW_SUPABASE_ANON_KEY  the project's anon key (public by design)
//   REVIEW_API_BASE           the review service on Railway, https://...
//   REVIEW_REQUIRE_MFA        optional; "off" turns two-step sign-in off (demo only), default on
//
// Never put a service key or a password in these variables.
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";

const env = (name) => (process.env[name] || "").trim();
const settings = {
  supabaseUrl: env("REVIEW_SUPABASE_URL").replace(/\/$/, ""),
  supabaseAnonKey: env("REVIEW_SUPABASE_ANON_KEY"),
  apiBase: env("REVIEW_API_BASE").replace(/\/$/, ""),
  // REVIEW_REQUIRE_MFA=off turns two-step sign-in off (demo only); anything else, or unset, keeps it on.
  // It must match the review service's REVIEW_REQUIRE_MFA.
  requireMfa: !["0", "off", "false", "no"].includes(env("REVIEW_REQUIRE_MFA").toLowerCase()),
};

const missing = Object.entries({ REVIEW_SUPABASE_URL: settings.supabaseUrl,
  REVIEW_SUPABASE_ANON_KEY: settings.supabaseAnonKey, REVIEW_API_BASE: settings.apiBase })
  .filter(([, value]) => !value).map(([name]) => name);
if (missing.length) {
  console.error(`Set ${missing.join(", ")} in the Vercel project's environment variables.`);
  process.exit(1);
}
for (const [name, value] of [["REVIEW_SUPABASE_URL", settings.supabaseUrl], ["REVIEW_API_BASE", settings.apiBase]]) {
  if (!value.startsWith("https://")) {
    console.error(`${name} must start with https://`);
    process.exit(1);
  }
}

mkdirSync("dist", { recursive: true });
for (const file of ["index.html", "review.css", "review.js"]) copyFileSync(file, `dist/${file}`);
writeFileSync("dist/config.js", `window.REVIEW_CONFIG = ${JSON.stringify(settings)};\n`);
console.log(`Review page built for API ${settings.apiBase}, two-step sign-in ${settings.requireMfa ? "on" : "OFF"}`);
