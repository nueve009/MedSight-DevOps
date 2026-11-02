import AsyncStorage from "@react-native-async-storage/async-storage";
import { createClient } from "@supabase/supabase-js";
import { AppState } from "react-native";

// ────────────────────────────────────────────
// SUPABASE CLIENT
// ────────────────────────────────────────────
// Single shared client for the whole app.
//
// Setup:
//   1. Copy .env.example → .env.local
//   2. Fill in EXPO_PUBLIC_SUPABASE_URL and EXPO_PUBLIC_SUPABASE_ANON_KEY
//      (Supabase dashboard → Project Settings → API)
//   3. Restart the dev server so Expo re-reads the env file.
//
// The anon key is intentionally public — it is safe to embed in the app
// bundle. Each install signs in anonymously (no login screen) and gets its
// own Supabase user ID; row-level security then lets it read and write only
// its own rows (supabase/anon_auth_rls.sql). Never put the service-role key here.

const supabaseUrl = process.env.EXPO_PUBLIC_SUPABASE_URL ?? "";
const supabaseAnonKey = process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ?? "";

if (!supabaseUrl || !supabaseAnonKey) {
  // Loud warning in dev; in production the queries will simply fail.
  console.warn(
    "[MedSight] Supabase env vars are not set.\n" +
      "Copy .env.example → .env.local and fill in:\n" +
      "  EXPO_PUBLIC_SUPABASE_URL\n" +
      "  EXPO_PUBLIC_SUPABASE_ANON_KEY\n" +
      "Then restart the dev server.",
  );
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: {
    // React Native has no window.localStorage; use AsyncStorage instead.
    storage: AsyncStorage,
    // Automatically refresh the session token before it expires.
    autoRefreshToken: true,
    // Keep the session across app restarts.
    persistSession: true,
    // Disable browser-based OAuth redirect detection (not needed on native).
    detectSessionInUrl: false,
  },
});

// Refresh the session token only while the app is in the foreground, as the
// Supabase React Native guide recommends.
AppState.addEventListener("change", (state) => {
  if (state === "active") {
    supabase.auth.startAutoRefresh();
  } else {
    supabase.auth.stopAutoRefresh();
  }
});

// ─── ANONYMOUS SIGN-IN ───

// Returns this install's Supabase user ID, signing in anonymously the first
// time. The session is kept in AsyncStorage, so the ID stays the same across
// restarts. Returns null (and logs) when signing in fails, e.g. offline.
let pendingSignIn: Promise<string | null> | null = null;

export async function getUserId(): Promise<string | null> {
  const { data } = await supabase.auth.getSession();
  if (data.session) {
    return data.session.user.id;
  }
  // Several screens may ask at once; sign in only once.
  pendingSignIn ??= supabase.auth.signInAnonymously().then(({ data: signIn, error }) => {
    pendingSignIn = null;
    if (error || !signIn.user) {
      console.error("[supabase] anonymous sign-in error:", error?.message);
      return null;
    }
    return signIn.user.id;
  });
  return pendingSignIn;
}
