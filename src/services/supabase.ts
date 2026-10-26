import AsyncStorage from "@react-native-async-storage/async-storage";
import { createClient } from "@supabase/supabase-js";

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
// bundle. Row-level security on the Supabase side controls what each device
// can actually read or write. Never put the service-role key here.

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
