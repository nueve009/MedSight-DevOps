import { Platform } from "react-native";

import { IdentifyResponse, ScanResult } from "@/types";
import { nextMockResponse } from "./mockData";

// ────────────────────────────────────────────
// API
// ────────────────────────────────────────────
// The ONLY file that talks to the backend. Screens call the functions here
// and never use fetch themselves.

// ─── CONFIG ───
// Values come from .env.local (see .env.example). Expo only reads variables
// that start with EXPO_PUBLIC_, and only when written exactly like this.

const API_BASE_URL = (process.env.EXPO_PUBLIC_API_BASE_URL ?? "").replace(
  /\/+$/,
  "",
);
const API_KEY = process.env.EXPO_PUBLIC_MEDSIGHT_API_KEY ?? "";

// No backend URL set = mock mode: fake replies from mockData.ts.
export const USE_MOCK = API_BASE_URL === "";

// The backend makes up to two LLM calls per scan, so allow it some time.
const TIMEOUT_MS = 30000;
// The backend rejects text longer than this.
const MAX_TEXT_LENGTH = 5000;
const APP_VERSION = "1.0.0";

// ─── ERRORS ───
// Every failure is thrown as an ApiError, so screens can check `error.kind`
// and decide what to show.

export type ApiErrorKind =
  | "network" // the phone could not reach the server at all
  | "timeout" // the server took too long
  | "server" // the server replied with an error, or with a reply we can't use
  | "cancelled"; // the user pressed Cancel

export class ApiError extends Error {
  kind: ApiErrorKind;
  // A sentence from the backend to read aloud, when it sent one.
  spokenText?: string;

  constructor(kind: ApiErrorKind, message: string, spokenText?: string) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.spokenText = spokenText;
    // Keeps `error instanceof ApiError` working after the code is compiled.
    Object.setPrototypeOf(this, ApiError.prototype);
  }
}

// ─── TRANSLATE: backend reply → what the screens use ───

function isIdentifyResponse(data: unknown): data is IdentifyResponse {
  if (typeof data !== "object" || data === null) {
    return false;
  }
  const reply = data as Record<string, unknown>;
  return (
    (reply.status === "ok" ||
      reply.status === "low_confidence" ||
      reply.status === "rescan") &&
    typeof reply.spoken_text === "string" &&
    typeof reply.grounded === "boolean"
  );
}

export function toScanResult(reply: IdentifyResponse): ScanResult {
  let status: ScanResult["status"];
  if (reply.status === "rescan") {
    status = "unreadable";
  } else if (reply.grounded && reply.status === "ok") {
    // Safety: "verified" needs BOTH a clear reading and a match in the
    // verified dataset. A shaky reading is never shown as verified.
    status = "verified";
  } else {
    status = "notVerified";
  }

  return {
    status,
    name: reply.brand_name ?? reply.generic_name ?? undefined,
    strength: reply.dosage ?? undefined,
    description: reply.description ?? undefined,
    spokenText: reply.spoken_text,
    warnings: Array.isArray(reply.warnings) ? reply.warnings : [],
  };
}

// ─── HELPERS ───

// Waits, but stops early (with a "cancelled" error) if the signal fires.
function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new ApiError("cancelled", "The scan was cancelled."));
    });
  });
}

// ─── IDENTIFY ───

// Sends the text read from the medicine box to the backend and returns the
// result for the Result screen. Pass an AbortSignal to make it cancellable.
export async function identifyMedicine(
  ocrText: string,
  signal?: AbortSignal,
): Promise<ScanResult> {
  if (signal?.aborted) {
    throw new ApiError("cancelled", "The scan was cancelled.");
  }

  // Mock mode: pretend the network takes 1.5 to 3 seconds.
  if (USE_MOCK) {
    await wait(1500 + Math.random() * 1500, signal);
    return toScanResult(nextMockResponse());
  }

  // Our own controller lets us stop the request for two reasons: the
  // timeout below, or the caller's signal (the Cancel button).
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, TIMEOUT_MS);
  const cancel = () => controller.abort();
  signal?.addEventListener("abort", cancel);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/identify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY,
      },
      body: JSON.stringify({
        text: ocrText.slice(0, MAX_TEXT_LENGTH),
        lines: [],
        ocr_confidence: null,
        platform: Platform.OS,
        app_version: APP_VERSION,
      }),
      signal: controller.signal,
    });
  } catch {
    // fetch only throws when no reply arrived at all.
    if (timedOut) {
      throw new ApiError("timeout", "The server took too long to reply.");
    }
    if (signal?.aborted) {
      throw new ApiError("cancelled", "The scan was cancelled.");
    }
    throw new ApiError("network", "Could not reach the server.");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }

  // A reply arrived. It can still be an error (401, 422, 503…) or not JSON.
  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const spokenText =
      typeof data === "object" &&
      data !== null &&
      typeof (data as Record<string, unknown>).spoken_text === "string"
        ? ((data as Record<string, unknown>).spoken_text as string)
        : undefined;
    throw new ApiError(
      "server",
      `The server replied with status ${response.status}.`,
      spokenText,
    );
  }

  if (!isIdentifyResponse(data)) {
    throw new ApiError("server", "The server sent a reply the app can't read.");
  }

  return toScanResult(data);
}

// ─── HEALTH ───

// true when the backend is up. Always true in mock mode.
export async function checkHealth(): Promise<boolean> {
  if (USE_MOCK) {
    return true;
  }
  try {
    const response = await fetch(`${API_BASE_URL}/health`);
    return response.ok;
  } catch {
    return false;
  }
}
