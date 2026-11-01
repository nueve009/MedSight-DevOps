import { HistoryItem, SavedMedicine, ScanResult, ScanStatus } from "@/types";
import { getUserId, supabase } from "./supabase";

// ────────────────────────────────────────────
// REMOTE DATABASE (Supabase)
// ────────────────────────────────────────────
// Stores the scan history and the caregiver's "My medicines" list in Supabase.
// Screens call the exported functions below and never query Supabase directly.
//
// Auth strategy: no login screen. Each install signs in anonymously
// (getUserId() in supabase.ts) and every row carries that Supabase user ID.
// Row-level security only lets a user read and write their own rows, so the
// user_id filters below are for clarity, not the protection itself.
//
// Table schema and security rules: supabase/anon_auth_rls.sql.

const HISTORY_LIMIT = 50;

// ─── HISTORY ───

// Supabase row shape for the history table.
type HistoryRow = {
  id: number;
  user_id: string;
  status: string;
  name: string | null;
  strength: string | null;
  scanned_at: string;
};

// Saves one scan to Supabase.
// The trim-to-50 is handled by a DB trigger (supabase/anon_auth_rls.sql).
// If the trigger is not installed, the oldest rows beyond the limit stay
// harmlessly — they won't be returned by getHistory().
export async function addHistory(result: ScanResult): Promise<void> {
  const user_id = await getUserId();
  if (!user_id) {
    return;
  }
  const { error } = await supabase.from("history").insert({
    user_id,
    status: result.status,
    name: result.name ?? null,
    strength: result.strength ?? null,
    scanned_at: new Date().toISOString(),
  });
  if (error) {
    console.error("[db] addHistory error:", error.message);
  }
}

// Newest scan first, capped at HISTORY_LIMIT rows for this user.
export async function getHistory(): Promise<HistoryItem[]> {
  const user_id = await getUserId();
  if (!user_id) {
    return [];
  }
  const { data, error } = await supabase
    .from("history")
    .select("id, status, name, strength, scanned_at")
    .eq("user_id", user_id)
    .order("scanned_at", { ascending: false })
    .limit(HISTORY_LIMIT);

  if (error) {
    console.error("[db] getHistory error:", error.message);
    return [];
  }

  return (data as HistoryRow[]).map((row) => ({
    id: row.id,
    status: row.status as ScanStatus,
    name: row.name ?? undefined,
    strength: row.strength ?? undefined,
    scannedAt: row.scanned_at,
  }));
}

// ─── MY MEDICINES ───

// Supabase row shape for the my_medicines table.
type MedicineRow = {
  id: number;
  user_id: string;
  name: string;
  dose_note: string | null;
};

function toSavedMedicine(row: MedicineRow): SavedMedicine {
  return {
    id: row.id,
    name: row.name,
    doseNote: row.dose_note ?? undefined,
  };
}

// Alphabetical, ignoring capital letters.
export async function getMyMedicines(): Promise<SavedMedicine[]> {
  const user_id = await getUserId();
  if (!user_id) {
    return [];
  }
  const { data, error } = await supabase
    .from("my_medicines")
    .select("id, name, dose_note")
    .eq("user_id", user_id)
    .order("name", { ascending: true });

  if (error) {
    console.error("[db] getMyMedicines error:", error.message);
    return [];
  }

  return (data as MedicineRow[]).map(toSavedMedicine);
}

export async function countMyMedicines(): Promise<number> {
  const user_id = await getUserId();
  if (!user_id) {
    return 0;
  }
  const { count, error } = await supabase
    .from("my_medicines")
    .select("id", { count: "exact", head: true })
    .eq("user_id", user_id);

  if (error) {
    console.error("[db] countMyMedicines error:", error.message);
    return 0;
  }

  return count ?? 0;
}

// Returns false (and inserts nothing) when the same name is already saved.
// The unique(user_id, name) DB constraint also enforces this on the server.
export async function addMyMedicine(
  name: string,
  doseNote?: string,
): Promise<boolean> {
  const user_id = await getUserId();
  if (!user_id) {
    return false;
  }

  // Check for an existing entry first (case-insensitive, matching SQLite behaviour).
  const { data: existing } = await supabase
    .from("my_medicines")
    .select("id")
    .eq("user_id", user_id)
    .ilike("name", name)
    .maybeSingle();

  if (existing) {
    return false;
  }

  const { error } = await supabase.from("my_medicines").insert({
    user_id,
    name,
    dose_note: doseNote || null,
  });

  if (error) {
    console.error("[db] addMyMedicine error:", error.message);
    return false;
  }

  return true;
}

export async function deleteMyMedicine(id: number): Promise<void> {
  const user_id = await getUserId();
  if (!user_id) {
    return;
  }
  const { error } = await supabase
    .from("my_medicines")
    .delete()
    .eq("id", id)
    .eq("user_id", user_id); // safety: only delete your own rows

  if (error) {
    console.error("[db] deleteMyMedicine error:", error.message);
  }
}

// Looks for a scanned medicine name in the saved list (case-insensitive).
// A saved "Biogesic 500 mg" matches a scanned "BIOGESIC": the saved name
// either equals the scanned name or starts with it followed by a space.
export async function findMyMedicine(
  scannedName: string,
): Promise<SavedMedicine | null> {
  const name = scannedName.trim();
  if (!name) {
    return null;
  }

  const user_id = await getUserId();
  if (!user_id) {
    return null;
  }

  // Exact match (case-insensitive).
  const { data: exact } = await supabase
    .from("my_medicines")
    .select("id, name, dose_note")
    .eq("user_id", user_id)
    .ilike("name", name)
    .limit(1)
    .maybeSingle();

  if (exact) {
    return toSavedMedicine(exact as MedicineRow);
  }

  // Prefix match: saved name starts with scanned name + space
  // e.g. saved "Biogesic 500 mg" matches scanned "BIOGESIC"
  const { data: prefix } = await supabase
    .from("my_medicines")
    .select("id, name, dose_note")
    .eq("user_id", user_id)
    .ilike("name", `${name} %`)
    .limit(1)
    .maybeSingle();

  return prefix ? toSavedMedicine(prefix as MedicineRow) : null;
}
