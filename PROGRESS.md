# MedSight — Project Progress

Shared status of the app. Update this when you finish or start something, so everyone (and any AI assistant) knows what exists and what's next.

## What MedSight is
An assistive mobile app that helps visually impaired users identify medicines with the phone camera. The user points the camera at a medicine box, the photo goes to our backend, and the app **speaks the result** and **vibrates** a pattern for the status. The screen is for low-vision users and sighted helpers: black background, one yellow accent, very large text.

Design mockups: `Sample UI/MedSight Screens.pdf`.

## Current state
- Expo SDK 57 + React Native + TypeScript, expo-router (stack). Screens live in `src/app/`.
- Design tokens, font and all planned reusable components are done (see "Reusable building blocks").
- **All 8 screens are built** from the PDF: Scan, Checking, Result (verified / not verified), Offline, History, Settings, My medicines, plus a Voice picker (not in the PDF). 
- **The scan flow is connected: Scan → Checking → Result (or Offline).** Scan checks the connection, Checking calls `identifyMedicine()` in `src/services/api.ts`, and the reply decides the screen. Cancel on Checking cancels the request.
- **The app runs in mock mode until a backend URL is set** in `.env.local` (copy `.env.example`). Mock mode cycles through three fake replies: not verified, verified, unreadable.
- **Live camera, but no OCR yet.** The Scan screen shows the back camera (`expo-camera` `CameraView`) and asks for camera permission ("Allow camera" button, spoken message). The Scan button does not take a photo yet: every scan sends the same sample label text from `src/services/ocr.ts`. The backend expects the phone to read the label text (OCR) and send only the text; that needs a development build, not Expo Go.
- Result reads its data from route params (`status`: `verified` / `notVerified` / `unreadable`, `name`, `strength`, optional `form`, `onList`, `doseNote`, `spokenText`). It speaks the backend's `spokenText` when present. "Verified" shows only when the backend says the label was read clearly **and** the medicine is in its verified dataset.
- Offline also shows server problems (`reason=server` param): different words and a warning icon.
- Settings live in `SettingsContext` (**in memory only, reset on restart**). Speech speed and Voice are used by `useSpeech`. Vibration and High contrast are stored but nothing reads them yet.
- **History and My medicines are stored in Supabase** (`src/services/db.ts` + `src/services/supabase.ts`). Every scan is saved remotely (newest 50 per device). On My medicines a caregiver can add a medicine (name + optional dose note) and remove one (with a confirmation). Settings shows the real saved count. When a scanned medicine's name matches a saved one, Result gets `onList` and the dose note. Each install is identified by a stable UUID stored in AsyncStorage (`@medsight/device_id`).
- Backend: separate repo (`MedSight-Backend`, FastAPI). `POST /identify` takes OCR text and returns the identification plus `spoken_text`. Its verified dataset is still empty, so the real backend currently answers "not verified" for every medicine.

## Reusable building blocks — use these, don't make new ones

| File | What it is | How to use it |
|---|---|---|
| `src/theme/theme.ts` | Design tokens: `colors`, `fonts`, `fontSizes`, `spacing`, `radii`, `sizes` | `import { colors, spacing } from "@/theme/theme"`. No hard-coded hex values or font sizes in components or screens. |
| `src/components/Screen.tsx` | Black, safe-area wrapper with 16px padding. Flex column that fills the screen | `<Screen>…</Screen>` as the outermost element of a screen |
| `src/components/AppText.tsx` | All text. Atkinson Hyperlegible font, white by default. Forwards extra `Text` props | `<AppText variant="h1" bold accessibilityRole="header">Title</AppText>` · `variant`: `display` 52, `h1` 40, `title` 30, `body` 24, `bodySm` 20, `caption` 18 · optional `bold`, `color`, `style` |
| `src/components/Icon.tsx` | Stroke icons, hidden from screen readers. Also re-exports the `IconName` type | `<Icon name="camera" size={32} color={colors.accent} />` · names: `camera`, `history`, `settings`, `speaker`, `check`, `warning`, `wifiOff`, `back`, `chevronRight`, `repeat` (add new ones in `iconPaths.ts`) |
| `src/components/PrimaryButton.tsx` | Main action: full-width yellow button, min height 96, light haptic. `icon` optional | `<PrimaryButton label="Scan" icon="camera" onPress={fn} accessibilityHint="…" />` |
| `src/components/SecondaryButton.tsx` | Outlined full-width button, min height 80, light haptic. `icon` optional | `<SecondaryButton label="Repeat" icon="repeat" onPress={fn} accessibilityHint="…" />` |
| `src/components/CornerButton.tsx` | Small raised button for the top corners (History, Settings, Back), min height 64, light haptic | `<CornerButton label="Back" icon="back" onPress={() => router.back()} accessibilityHint={messages.common.backHint} />` · always pass the hint |
| `src/components/ScanFrame.tsx` | Four yellow corner brackets over the camera. Fills its parent, no props | `<View style={{ flex: 1 }}><ScanFrame /></View>` |
| `src/components/GuidancePill.tsx` | Hint with speaker icon. `pill` (default): big black pill with yellow border, announces text changes. `caption`: small muted icon + text, hidden from screen readers | `<GuidancePill text="Move closer" />` · `<GuidancePill variant="caption" text="Tap anywhere to hear it again" />` |
| `src/components/StatusBanner.tsx` | Full-width colored banner with badge icon + word. Exports the `StatusTone` type (`"verified" \| "warning"`) | `<StatusBanner tone="verified" label="Verified" />`. Pass labels in normal case; the component uppercases them visually. Render it with `marginHorizontal: -spacing.md` inside `Screen` to run edge to edge. |
| `src/components/InfoCard.tsx` | Dark card with colored border, small colored label and body lines | `<InfoCard tone="warning" label="What to do" lines={["…", "…"]} />`. Lines are used as React keys, so keep them unique. |
| `src/components/ListRow.tsx` | Settings-style row: bold label, optional muted value. With `onPress`: button with right chevron. Without: display-only. Min height 64 | `<ListRow label="Voice" value="Phone default" onPress={fn} />` |
| `src/components/HistoryRow.tsx` | Display-only history row: status badge, title, "Status · time". Min height 100 | `<HistoryRow tone="verified" title="Paracetamol 500 mg" statusLabel="Verified" time="Today, 8:14 AM" />` |
| `src/components/ToggleRow.tsx` | Switch row (`switch` role): label, "On"/"Off" word, yellow toggle. Min height 80 | `<ToggleRow label="Vibration" value={on} onValueChange={setOn} stateLabel={on ? messages.settings.on : messages.settings.off} />` |
| `src/components/SegmentedControl.tsx` | Generic single-choice control (`radiogroup`), min height 64 per segment | `<SegmentedControl label="Speech speed" options={[{ label: "Slow", value: "slow" }, …]} value={speed} onChange={setSpeed} />` |
| `src/components/OptionRow.tsx` | Selectable list row (`radio` role): title, optional description, check icon + yellow border when selected. Min height 80 | `<OptionRow label="Samantha" description="English (US)" selected={on} onPress={fn} />` |
| `src/context/SettingsContext.tsx` | `SettingsProvider` (wraps the app in `_layout.tsx`) and `useSettings()`: `speechSpeed`, `vibration`, `highContrast`, `voice` and their setters. In memory only | `const { speechSpeed, voice } = useSettings()` |
| `src/hooks/useSpeech.ts` | Speaks text with the saved voice and speed. `speak` and `stop` are stable, safe in effect dependencies | `const { speak, stop } = useSpeech(); speak(text)` · never call `expo-speech` directly in screens |
| `src/services/api.ts` | The **only** file that talks to the backend. `identifyMedicine(text, signal)` returns a `ScanResult`; failures are thrown as `ApiError` with a `kind` (`network`, `timeout`, `server`, `cancelled`) | `const result = await identifyMedicine(text, controller.signal)` · never call `fetch` in a screen |
| `src/types/index.ts` | Shared types: `IdentifyResponse` (the backend's reply), `ScanResult` and `ScanStatus` (what screens use) | `import { ScanResult } from "@/types"` |
| `src/services/ocr.ts` | `readLabelText()`: returns the text on the medicine box. **Temporary**: returns a sample label until real OCR is added | `const text = await readLabelText()` |
| `src/services/mockData.ts` | Fake backend replies for mock mode | used only by `api.ts` |
| `src/services/db.ts` | Local storage (expo-sqlite): `addHistory`, `getHistory`, `getMyMedicines`, `addMyMedicine`, `deleteMyMedicine`, `findMyMedicine`, `countMyMedicines`. **Interim**: to be replaced by backend API calls | `const items = await getHistory()` · never write SQL in a screen |
| `src/components/TextField.tsx` | Labelled one-line text box with optional helper text | `<TextField label="Medicine name" value={name} onChangeText={setName} />` |
| `src/components/MedicineRow.tsx` | Saved-medicine row: name, optional dose note, Remove button | `<MedicineRow name="…" doseNote="…" removeLabel="Remove" removeAccessibilityLabel="Remove …" onRemove={fn} />` |
| `src/constants/messages.ts` | Every visible and spoken string | `import { messages } from "@/constants/messages"` then `messages.scan.instruction`. Add new strings here, not inside screens. |

## Conventions
- Styles go in a `StyleSheet.create()` at the bottom of each file, one property per line, using theme tokens.
- Never use the raw `<Text>`; use `AppText` (fonts don't inherit in React Native).
- Never show a status by color alone: icon + word + color.
- Text is never smaller than 18. Touch targets are at least 64 tall; main actions 80–96.
- Every touchable has `accessibilityRole` and `accessibilityLabel`. One documented exception: the "tap anywhere to repeat" wrapper on Result is hidden from screen readers, which use the Repeat button.
- Screen titles use `<AppText accessibilityRole="header" style={{ flexShrink: 1 }}>` so long titles wrap.
- Main action at the bottom, full width. Secondary actions in the top corners.
- Import with the `@/` alias (`@/` = `src/`).
- Install packages with `npx expo install <package>`, not `npm install`.
- Before committing, run `npx expo lint` and `npx tsc --noEmit`.

## Git workflow
- `main` is protected: nobody pushes to it directly. Changes reach `main` only through a pull request that @BlinkyPanoptes reviews and merges.
- `dev` is the shared working branch. Start your work from `dev`: `git switch dev`, `git pull`, then `git switch -c your-feature-name`.
- When your part works, push your branch and open a pull request **into `dev`**.
- `dev` is merged into `main` by pull request once it is checked and working.

## Milestones
| # | Milestone | Status |
|---|---|---|
| 1 | Foundation: clean project, `theme.ts`, `AppText`, `Screen`, `Icon`, buttons, empty screens | ✅ Done |
| 2 | More components: `InfoCard`, `StatusBanner`, `GuidancePill`, `ScanFrame`, `ListRow` (+ `HistoryRow`) | ✅ Done |
| 3 | Settings + feedback: `ToggleRow`, `SegmentedControl`, `SettingsContext`, speech | 🟡 In progress (components, context and `useSpeech` done; haptics via `feedback.ts` respecting Vibration, saving settings, and High contrast still to do) |
| 4 | Scan → Checking → Result (mock API) | 🟡 In progress (flow connected through `api.ts` with mock and real modes, tested against the real backend; live camera added; photo capture and OCR still to do) |
| 5 | Offline + errors | 🟡 In progress (Scan routes to Offline when there is no internet; network, timeout and server errors are handled; not yet tested against the real backend) |
| 6 | History | ✅ Done (scans saved to Supabase; newest 50 per device; rows listed newest-first) |
| 7 | My medicines (caregiver) | ✅ Done (add and remove work, stored in Supabase; no editing yet) |
| 8 | Polish | ⬜ Not started |
| 9 | Real API | ⬜ Not started |

This plan was made before the CSP221A project guide. It still needs to be checked against the guide (web deployment, CRUD through the backend API, CI/CD).

## Done
- Removed the Expo template examples; created empty files for the full folder structure
- `src/app/_layout.tsx`: root Stack, header hidden, loads the Atkinson Hyperlegible font, wraps the app in `SettingsProvider`
- `src/app/index.tsx`: Scan screen (History + Settings buttons, camera placeholder card, scan frame, guidance pill, Scan button)
- Screens: `checking.tsx`, `result.tsx` (verified / not verified, speaks the result, tap anywhere or Repeat to hear it again), `offline.tsx`, `history.tsx`, `settings.tsx`, `my-medicines.tsx`, `voice.tsx` (lists the voices installed on the phone, English only, plays a sample when one is picked)
- `src/theme/theme.ts`: design tokens
- Components: `AppText`, `Screen`, `Icon` + `iconPaths.ts`, `PrimaryButton`, `SecondaryButton`, `CornerButton`, `ScanFrame`, `GuidancePill`, `StatusBanner`, `InfoCard`, `ListRow`, `HistoryRow`, `ToggleRow`, `SegmentedControl`, `OptionRow`
- `src/context/SettingsContext.tsx`, `src/hooks/useSpeech.ts`, `src/services/mockMedicines.ts`
- `src/constants/messages.ts`: strings for all screens, including spoken text and accessibility hints
- Setup: `expo-camera`, `expo-speech`, `expo-haptics`, `expo-sqlite`, `expo-image-manipulator`, `expo-crypto`, `@react-native-community/netinfo`, `@supabase/supabase-js`, `@react-native-async-storage/async-storage`, `react-native-svg`, the Atkinson font package, and ESLint (`eslint.config.js`). `npx expo lint`, `npx tsc --noEmit` and `npx expo-doctor` passed at setup; re-run them before the next commit.

- Promo video (24 s, landscape, 3D iPhone model, generated music): a separate Remotion project in the sibling folder `MedSight-video` (not in this repo). The Scan screen is a picture; Checking and the two Result screens are redrawn as web components with values copied from `theme.ts` and `messages.ts`, so update the copies there if the app changes. Render with `npm run render`.

## In progress
- (nothing)

## Up next
- **Supabase setup**: run the SQL in `supabase_schema.md` (via the Supabase dashboard SQL Editor), then add `EXPO_PUBLIC_SUPABASE_URL` and `EXPO_PUBLIC_SUPABASE_ANON_KEY` to `.env.local` and restart the dev server
- Test against the real FastAPI backend: set `EXPO_PUBLIC_API_BASE_URL` in `.env.local` once it is running or deployed
- Take the photo on Scan and run real OCR inside `readLabelText()` in `src/services/ocr.ts` (needs a development build, not Expo Go)
- Show the backend's medicine name and description on the Not verified screen? Today it is only spoken
- `feedback.ts`: haptic patterns for verified / not verified, respecting the Vibration setting (the button haptics also ignore it today)
- Save settings so they survive an app restart; apply High contrast
- "Not on your list" warning on Result for a verified medicine that isn't saved (the list exists now)
- Optional cleanup: add a `borderWidths` token (2, 3, 4 and 6 are hard-coded in components and screens) and move the repeated badge sizes (56 / 36) in `StatusBanner` and `HistoryRow` into `theme.ts`; add an `edges` option to `Screen` so the Result banner can fill the status bar area

## Open questions
- Does the speech engine read "mg" correctly on real devices? If it spells "m g", convert to "milligrams" in the spoken text only.
- Tune the speech rates in `useSpeech.ts` (slow 0.75, normal 1, fast 1.3) on real devices.