import { IdentifyResponse } from "@/types";

// ────────────────────────────────────────────
// MOCK DATA
// ────────────────────────────────────────────
// Fake backend replies, in the backend's own shape, used when no backend
// URL is set (see api.ts). Each scan returns the next one in the list, so
// every result screen can be demoed without a server.
//
// Synthetic content only. These are not real medical instructions.

const MOCK_RESPONSES: IdentifyResponse[] = [
  // 1. Read clearly, but not in the verified dataset. This is what the real
  //    backend returns for every medicine until its dataset is filled in.
  {
    status: "ok",
    brand_name: "Biogesic",
    generic_name: "Paracetamol",
    dosage: "500mg",
    description: "[General description written by the backend]",
    grounded: false,
    source_url: null,
    warnings: ["not verified"],
    spoken_text:
      "Biogesic. Warning: this medicine is not in my verified list, so this is general information only and may be inaccurate. [General description written by the backend]. Please check the label or ask a pharmacist before taking it.",
    cache: "miss",
  },
  // 2. Read clearly and found in the verified dataset.
  {
    status: "ok",
    brand_name: "Biogesic",
    generic_name: "Paracetamol",
    dosage: "500mg",
    description: "[Verified description from the dataset]",
    grounded: true,
    source_url: null,
    warnings: [],
    spoken_text: "Biogesic. [Verified description from the dataset].",
    cache: "hit",
  },
  // 3. Nothing readable on the box.
  {
    status: "rescan",
    brand_name: null,
    generic_name: null,
    dosage: null,
    description: null,
    grounded: false,
    source_url: null,
    warnings: [],
    spoken_text:
      "I couldn't read any text on that packaging. Please try again with better lighting or a closer shot.",
    cache: null,
  },
];

let nextIndex = 0;

export function nextMockResponse(): IdentifyResponse {
  const response = MOCK_RESPONSES[nextIndex];
  nextIndex = (nextIndex + 1) % MOCK_RESPONSES.length;
  return response;
}
