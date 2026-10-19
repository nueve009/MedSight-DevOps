// ────────────────────────────────────────────
// SHARED TYPES
// ────────────────────────────────────────────

// ─── What the backend sends (POST /identify) ───
// Field names match the backend exactly. See the "API contract" section of
// the MedSight-Backend README.

export type IdentifyResponse = {
  // How well the label was read.
  status: "ok" | "low_confidence" | "rescan";
  brand_name: string | null;
  generic_name: string | null;
  dosage: string | null;
  description: string | null;
  // true only when the medicine is in the backend's verified dataset.
  grounded: boolean;
  source_url: string | null;
  warnings: string[];
  // The exact sentence to read aloud. It already includes the safety warnings.
  spoken_text: string;
  cache: "hit" | "miss" | null;
};

// ─── What the app's screens use ───

export type ScanStatus =
  | "verified" // read clearly AND found in the verified dataset
  | "notVerified" // a medicine name was read, but it can't be trusted
  | "unreadable"; // no text, or no medicine name, was found

export type ScanResult = {
  status: ScanStatus;
  name?: string; // brand name if there is one, otherwise the generic name
  strength?: string; // e.g. "500mg"
  description?: string;
  spokenText: string;
  warnings: string[];
};

// ─── What is saved on the phone (see src/services/db.ts) ───

// One past scan, shown on the History screen.
export type HistoryItem = {
  id: number;
  status: ScanStatus;
  name?: string;
  strength?: string;
  scannedAt: string; // ISO date, e.g. "2026-10-12T08:14:00.000Z"
};

// One medicine a caregiver saved on the My medicines screen.
export type SavedMedicine = {
  id: number;
  name: string;
  doseNote?: string;
};
