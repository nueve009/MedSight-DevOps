// ────────────────────────────────────────────
// OCR (reading the text on the medicine box)
// ────────────────────────────────────────────
// TEMPORARY STAND-IN. The backend expects the phone to read the label text
// itself and send only the text. Until on-device OCR is added (it needs a
// development build, not Expo Go), every scan "reads" this sample label so
// the rest of the flow can be built and tested.
//
// To finish this: take the photo with expo-camera, run OCR on it, and return
// the recognized text from readLabelText(). Nothing else has to change.

const SAMPLE_LABEL_TEXT = [
  "BIOGESIC",
  "Paracetamol",
  "500 mg Tablet",
  "Analgesic / Antipyretic",
].join("\n");

export async function readLabelText(): Promise<string> {
  return SAMPLE_LABEL_TEXT;
}
