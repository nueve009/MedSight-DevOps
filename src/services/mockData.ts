import { IdentifyResponse } from "@/types";

// ────────────────────────────────────────────
// MOCK DATA
// ────────────────────────────────────────────
// Fake backend replies, in the backend's own shape (API contract v2), used
// when no backend URL is set (see api.ts). Each scan returns the next one in
// the list, so every result screen can be demoed without a server.
//
// Synthetic content only. These are not real medical instructions.

const MOCK_RESPONSES: IdentifyResponse[] = [
  // 1. Read clearly, but not in the reviewed dataset (general knowledge).
  {
    status: "ok",
    rescan_reason: null,
    hint: null,
    tier: "general",
    product: {
      brand: "Biogesic",
      generics: ["Paracetamol"],
      strengths: ["500 mg"],
      prescription: false,
    },
    description: "[General description written by the backend]",
    sources: [],
    spoken_text:
      "Biogesic, Paracetamol 500 milligrams. Warning: this medicine isn't in our reviewed list, so this is general information only and may be inaccurate. [General description written by the backend].",
    request_id: null,
    cache: "miss",
  },
  // 2. Read clearly and found in the team-reviewed dataset.
  {
    status: "ok",
    rescan_reason: null,
    hint: null,
    tier: "reviewed",
    product: {
      brand: "Biogesic",
      generics: ["Paracetamol"],
      strengths: ["500 mg"],
      prescription: false,
    },
    description: "[Reviewed description from the dataset]",
    sources: [],
    spoken_text:
      "Biogesic, Paracetamol 500 milligrams. Our team has reviewed this information. [Reviewed description from the dataset].",
    request_id: null,
    cache: null,
  },
  // 3. Nothing readable on the box.
  {
    status: "rescan",
    rescan_reason: "no_text",
    hint: "more_light",
    tier: null,
    product: null,
    description: null,
    sources: [],
    spoken_text:
      "I couldn't read any text on that packaging. Please try again with more light.",
    request_id: null,
    cache: null,
  },
];

let nextIndex = 0;

export function nextMockResponse(): IdentifyResponse {
  const response = MOCK_RESPONSES[nextIndex];
  nextIndex = (nextIndex + 1) % MOCK_RESPONSES.length;
  return response;
}
