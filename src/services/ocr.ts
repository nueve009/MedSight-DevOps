import { requireOptionalNativeModule } from "expo";
import { Platform } from "react-native";

// ────────────────────────────────────────────
// OCR (reading the text on the medicine box)
// ────────────────────────────────────────────
// Reads the text in a photo on the phone itself (expo-text-extractor: Google
// ML Kit on Android, Apple Vision on iOS). The backend only gets the text,
// never the photo.
//
// The native module only exists in a development or release build. In Expo
// Go and on the web it is missing, so every scan falls back to a sample
// label and the rest of the app can still be demoed there.

type TextExtractorModule = {
  // One string per block of text found in the picture.
  extractTextFromImage: (path: string) => Promise<string[]>;
};

// Loaded this way (not with `import`) so Expo Go and the web don't crash.
const extractor =
  requireOptionalNativeModule<TextExtractorModule>("ExpoTextExtractor");

// false in Expo Go and on the web: scans use the sample label.
export const OCR_AVAILABLE = extractor !== null;

const SAMPLE_LABEL_TEXT = [
  "BIOGESIC",
  "Paracetamol",
  "500 mg Tablet",
  "Analgesic / Antipyretic",
].join("\n");

// Returns the text on the medicine box in the photo, one block per line.
// An empty string means no text was found; the backend then asks for a
// rescan with a spoken hint.
export async function readLabelText(photoUri: string): Promise<string> {
  if (!extractor) {
    return SAMPLE_LABEL_TEXT;
  }

  // The Android module opens the image as a plain file path and fails on
  // "file://…" URIs. iOS takes a URL, so it keeps the prefix.
  const path =
    Platform.OS === "android" ? photoUri.replace(/^file:\/\//, "") : photoUri;

  const blocks = await extractor.extractTextFromImage(path);
  return blocks
    .map((block) => block.trim())
    .filter(Boolean)
    .join("\n");
}
