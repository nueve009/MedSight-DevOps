// ────────────────────────────────────────────
// SHARED TYPES
// ────────────────────────────────────────────

// ─── What the backend sends (POST /v2/identify) ───
// Field names match the backend exactly. See "API contract v2" in
// backend/README.md.

// Where the description comes from. Only "reviewed" is the team-checked
// dataset; the others are spoken with a warning by the backend.
export type AnswerTier = "reviewed" | "ingredient" | "web" | "general";

export type IdentifyResponse = {
  // ok: answered. ok_caveat: answered, but the reading was shaky.
  // rescan / multiple / gave_up: no answer; spoken_text says what to do.
  status: "ok" | "ok_caveat" | "rescan" | "multiple" | "gave_up";
  rescan_reason: string | null;
  hint: "move_closer" | "more_light" | "turn_box" | null;
  // null unless status is ok or ok_caveat.
  tier: AnswerTier | null;
  // null unless status is ok or ok_caveat. generics and strengths are
  // paired by position (a combination product lists every ingredient).
  product: {
    brand: string | null;
    generics: string[];
    strengths: string[];
    prescription: boolean;
  } | null;
  description: string | null;
  sources: string[];
  // The exact sentence to read aloud. It already includes the safety warnings.
  spoken_text: string;
  request_id: string | null;
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
  tier?: AnswerTier; // where the description comes from, when answered
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
