import * as SQLite from "expo-sqlite";

import { HistoryItem, SavedMedicine, ScanResult, ScanStatus } from "@/types";

// ────────────────────────────────────────────
// LOCAL DATABASE (on the phone)
// ────────────────────────────────────────────
// Stores the scan history and the caregiver's "My medicines" list in a
// SQLite file on the phone. Screens call the functions below and never
// write SQL themselves.
//
// INTERIM: the course guide requires this data to live on the backend and be
// reached through its API. When the backend has those endpoints, replace the
// body of each function with a call in api.ts. The screens won't change.

const HISTORY_LIMIT = 50;

// ─── SETUP ───

let database: Promise<SQLite.SQLiteDatabase> | null = null;

// Opens the database once and creates the tables the first time.
function getDatabase(): Promise<SQLite.SQLiteDatabase> {
  if (!database) {
    database = (async () => {
      const db = await SQLite.openDatabaseAsync("medsight.db");
      await db.execAsync(`
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          status TEXT NOT NULL,
          name TEXT,
          strength TEXT,
          scanned_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS my_medicines (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          dose_note TEXT
        );
      `);
      return db;
    })();
  }
  return database;
}

// ─── HISTORY ───

type HistoryRecord = {
  id: number;
  status: string;
  name: string | null;
  strength: string | null;
  scanned_at: string;
};

// Saves one scan, then trims the table to the newest 50.
export async function addHistory(result: ScanResult): Promise<void> {
  const db = await getDatabase();
  await db.runAsync(
    "INSERT INTO history (status, name, strength, scanned_at) VALUES (?, ?, ?, ?)",
    result.status,
    result.name ?? null,
    result.strength ?? null,
    new Date().toISOString(),
  );
  await db.runAsync(
    "DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT ?)",
    HISTORY_LIMIT,
  );
}

// Newest scan first.
export async function getHistory(): Promise<HistoryItem[]> {
  const db = await getDatabase();
  const rows = await db.getAllAsync<HistoryRecord>(
    "SELECT * FROM history ORDER BY id DESC LIMIT ?",
    HISTORY_LIMIT,
  );
  return rows.map((row) => ({
    id: row.id,
    status: row.status as ScanStatus,
    name: row.name ?? undefined,
    strength: row.strength ?? undefined,
    scannedAt: row.scanned_at,
  }));
}

// ─── MY MEDICINES ───

type MedicineRecord = {
  id: number;
  name: string;
  dose_note: string | null;
};

function toSavedMedicine(row: MedicineRecord): SavedMedicine {
  return {
    id: row.id,
    name: row.name,
    doseNote: row.dose_note ?? undefined,
  };
}

// Alphabetical, ignoring capital letters.
export async function getMyMedicines(): Promise<SavedMedicine[]> {
  const db = await getDatabase();
  const rows = await db.getAllAsync<MedicineRecord>(
    "SELECT * FROM my_medicines ORDER BY name COLLATE NOCASE",
  );
  return rows.map(toSavedMedicine);
}

export async function countMyMedicines(): Promise<number> {
  const db = await getDatabase();
  const row = await db.getFirstAsync<{ total: number }>(
    "SELECT COUNT(*) AS total FROM my_medicines",
  );
  return row?.total ?? 0;
}

// Returns false (and saves nothing) when the same name is already saved.
export async function addMyMedicine(
  name: string,
  doseNote?: string,
): Promise<boolean> {
  const db = await getDatabase();
  const existing = await db.getFirstAsync<{ id: number }>(
    "SELECT id FROM my_medicines WHERE name = ? COLLATE NOCASE",
    name,
  );
  if (existing) {
    return false;
  }
  await db.runAsync(
    "INSERT INTO my_medicines (name, dose_note) VALUES (?, ?)",
    name,
    doseNote || null,
  );
  return true;
}

export async function deleteMyMedicine(id: number): Promise<void> {
  const db = await getDatabase();
  await db.runAsync("DELETE FROM my_medicines WHERE id = ?", id);
}

// Looks for a scanned medicine in the saved list, ignoring capital letters.
// A saved "Biogesic 500 mg" matches a scanned "BIOGESIC": the saved name
// either equals the scanned name or starts with it followed by a space.
export async function findMyMedicine(
  scannedName: string,
): Promise<SavedMedicine | null> {
  const name = scannedName.trim();
  if (!name) {
    return null;
  }
  const db = await getDatabase();
  const row = await db.getFirstAsync<MedicineRecord>(
    "SELECT * FROM my_medicines WHERE LOWER(name) = LOWER(?) OR LOWER(name) LIKE LOWER(?) || ' %' LIMIT 1",
    name,
    name,
  );
  return row ? toSavedMedicine(row) : null;
}
