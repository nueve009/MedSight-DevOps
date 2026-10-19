// ────────────────────────────────────────────
// MESSAGES
// ────────────────────────────────────────────
// Every visible and spoken string lives here, so what the app shows and
// what it says always match. Add new screens as new groups.
//
// Rules:
// - Labels are written in normal case. Components apply uppercase
//   visually (StatusBanner, InfoCard), so screen readers read normal words.
// - Strings that are both shown and spoken are defined once below and
//   reused, so the two can never drift apart.
// - Anything that depends on data is a small function.
// - Do not add `as const` to the object: the list-style strings must stay
//   plain `string[]` to be accepted by components like InfoCard.

// ─── Shared pieces (shown on screen AND spoken) ───

const checkingTitle = "Checking…";
const checkingBody = "Hold still. This takes a few seconds.";

const onYourList = "On your list";

const notVerifiedBanner = "Not verified";
const notVerifiedHeadline = "Do not rely on this result.";
const notVerifiedBody = "MedSight could not match this medicine to its list.";
const notVerifiedSteps = [
  "Try again in brighter light, with the label facing the camera.",
  "Or ask someone you trust to check it.",
];

const cameraNeededTitle = "Camera access needed";
const cameraNeededBody = "MedSight uses the camera to read the medicine box.";

const offlineTitle = "No internet";
const offlineBody = "MedSight needs internet to identify medicines.";

const unreadableBanner = "Could not read";
const unreadableHeadline = "Couldn’t read the label.";
const unreadableBody = "MedSight could not find a medicine name on the box.";

const serverErrorTitle = "Server problem";
const serverErrorBody = "MedSight could not reach its server. Try again in a moment.";

const addToMyMedicines = "Add to my medicines";
const phoneDefaultVoice = "Phone default";

export const messages = {
  // ─── Scan screen ───
  scan: {
    instruction: "Point the camera at the medicine box",
    hint: "Hold the box inside the frame",
    scanButton: "Scan",
    scanButtonHint: "Takes a photo of the medicine box",
    // Shown instead of the camera until the user allows camera access.
    cameraNeededTitle: cameraNeededTitle,
    cameraNeededBody: cameraNeededBody,
    cameraNeededSpoken: `${cameraNeededTitle}. ${cameraNeededBody}`,
    allowCamera: "Allow camera",
    allowCameraHint: "Asks for permission to use the camera",
    history: "History",
    historyHint: "Shows the medicines you scanned before",
    settings: "Settings",
    settingsHint: "Opens speech, vibration and display settings",
  },

  // ─── Shared ───
  common: {
    back: "Back",
    backHint: "Returns to the previous screen",
  },

  // ─── Checking screen ───
  checking: {
    title: checkingTitle,
    body: checkingBody,
    // Spoken when the screen opens (the ellipsis is dropped for speech).
    spoken: "Checking. Hold still. This takes a few seconds.",
    // The animated yellow circle is the only non-text element.
    indicatorLabel: "Checking the medicine",
    cancel: "Cancel",
    cancelHint: "Stops checking and returns to the camera",
  },

  // ─── Result screen ───
  result: {
    verified: {
      banner: "Verified",
      onListLabel: onYourList,
      // Shown under the medicine name, e.g. "500 mg · Tablet"
      details: (strength: string, form?: string) =>
        form ? `${strength} · ${form}` : strength,
      // Spoken when the result appears and when the user repeats it,
      // e.g. "Verified. Paracetamol. 500 mg. Tablet." Periods make the
      // voice pause between the parts.
      spoken: (info: {
        name: string;
        strength: string;
        form?: string;
        onList: boolean;
        doseNote?: string;
      }) =>
        [
          "Verified.",
          `${info.name}.`,
          `${info.strength}.`,
          info.form ? `${info.form}.` : "",
          info.onList ? `${onYourList}.` : "",
          info.doseNote ?? "",
        ]
          .filter(Boolean)
          .join(" "),
    },
    notVerified: {
      banner: notVerifiedBanner,
      headline: notVerifiedHeadline,
      body: notVerifiedBody,
      whatToDoLabel: "What to do",
      whatToDoLines: notVerifiedSteps,
      tryAgain: "Try again",
      tryAgainHint: "Returns to the camera to scan the medicine again",
      spoken: [
        `${notVerifiedBanner}.`,
        notVerifiedHeadline,
        notVerifiedBody,
        ...notVerifiedSteps,
      ].join(" "),
    },
    // The label could not be read at all (no text, or no medicine name).
    unreadable: {
      banner: unreadableBanner,
      headline: unreadableHeadline,
      body: unreadableBody,
      spoken: `${unreadableHeadline} ${unreadableBody} ${notVerifiedSteps.join(" ")}`,
    },
    tapToRepeat: "Tap anywhere to hear it again",
    repeat: "Repeat",
    repeatHint: "Reads the result out loud again",
    scanAgain: "Scan again",
    scanAgainHint: "Returns to the camera to scan another medicine",
  },

  // ─── Offline screen ───
  offline: {
    title: offlineTitle,
    body: offlineBody,
    spoken: `${offlineTitle}. ${offlineBody}`,
    openHistory: "Open history",
    openHistoryHint: "Shows the medicines you scanned before",
    tryAgain: "Try again",
    tryAgainHint: "Checks the connection and returns to the camera",
  },

  // ─── Server problem (shown on the Offline screen layout) ───
  serverError: {
    title: serverErrorTitle,
    body: serverErrorBody,
    spoken: `${serverErrorTitle}. ${serverErrorBody}`,
  },

  // ─── History screen ───
  history: {
    title: "History",
    listLabel: "Scan history",
    empty: "No scans yet.",
    emptyHint: "Scan a medicine and it will appear here.",
    // Row titles for scans that have no medicine name
    unknownMedicine: "Unknown medicine",
    couldNotRead: "Could not read",
    // The status word shown in each row (never color alone)
    status: {
      verified: "Verified",
      notVerified: "Not verified",
      tryAgain: "Try again",
    },
    // Day words and the combined date text, e.g. "Today, 8:14 AM"
    today: "Today",
    yesterday: "Yesterday",
    dateTime: (day: string, time: string) => `${day}, ${time}`,
  },

  // ─── Settings screen ───
  settings: {
    title: "Settings",
    on: "On",
    off: "Off",
    speechSpeed: "Speech speed",
    speeds: {
      slow: "Slow",
      normal: "Normal",
      fast: "Fast",
    },
    vibration: "Vibration",
    vibrationHint: "Turns vibration feedback on or off",
    highContrast: "High contrast",
    highContrastHint: "Turns the high contrast display on or off",
    voice: "Voice",
    voiceDefault: phoneDefaultVoice,
    voiceHint: "Opens the list of voices that can read results out loud",
    caregiversHeading: "For caregivers",
    myMedicines: "My medicines",
    myMedicinesSaved: (count: number) =>
      count === 0 ? "None saved" : `${count} saved`,
    myMedicinesHint: "Opens the list of medicines saved by a caregiver",
    myMedicinesHelp: `Medicines saved here are marked “${onYourList}” when they are scanned.`,
  },

  // ─── Voice screen ───
  voice: {
    title: "Voice",
    listLabel: "Available voices",
    phoneDefault: phoneDefaultVoice,
    loading: "Loading voices…",
    noOtherVoices: "No other English voices were found on this phone.",
    selectHint: "Selects this voice and plays a short sample",
    enhanced: "Enhanced",
    genericName: (region: string, number: number) =>
      `${region} voice ${number}`,
    // Spoken as the preview when a voice is selected.
    sample: "This is how MedSight will read results out loud.",
    help: "Tap a voice to hear it. You can add more voices in your phone's text-to-speech settings.",
    // Display names for voice language tags. Unknown tags show as-is.
    regions: {
      "en-US": "English (US)",
      "en-GB": "English (UK)",
      "en-AU": "English (Australia)",
      "en-IN": "English (India)",
      "en-PH": "English (Philippines)",
      "en-CA": "English (Canada)",
      "en-IE": "English (Ireland)",
      "en-ZA": "English (South Africa)",
      "en-NZ": "English (New Zealand)",
      "en-SG": "English (Singapore)",
    } as Record<string, string>,
  },

  // ─── My medicines screen (caregiver) ───
  myMedicines: {
    title: "My medicines",
    listLabel: "Saved medicines",
    empty: "No medicines saved yet.",
    emptyHint: "Add the medicines this person takes, using the form above.",
    nameLabel: "Medicine name",
    nameHelper: "As written on the box: the brand and the strength.",
    doseNoteHelper: "Optional. A reminder that is read out with this medicine.",
    nameMissing: "Type the medicine name first.",
    alreadySaved: (name: string) => `${name} is already on the list.`,
    saveFailed: "Could not save. Please try again.",
    removeConfirmTitle: (name: string) => `Remove ${name}?`,
    removeConfirmBody: "It will no longer be marked as on your list.",
    removeCancel: "Cancel",
    add: addToMyMedicines,
    addHint: "Saves this medicine to the list MedSight checks against",
    added: (name: string) => `${name} added to my medicines.`,
    remove: "Remove",
    removeLabel: (name: string) => `Remove ${name}`,
    removeHint: "Takes this medicine off the list",
    removed: (name: string) => `${name} removed from my medicines.`,
    doseNoteLabel: "Dose note",
  },
};