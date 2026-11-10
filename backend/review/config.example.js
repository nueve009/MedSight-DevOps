// For a hosted copy of the page: copy to config.js and fill in. The local console
// (python -m scripts.review_console) writes these values itself.
// The anon key is public by design. Never put a service key or password here.
window.REVIEW_CONFIG = {
  supabaseUrl: "https://<project-ref>.supabase.co",
  supabaseAnonKey: "<anon key>",
  apiBase: "https://<your backend host>",
  requireMfa: true,
};
