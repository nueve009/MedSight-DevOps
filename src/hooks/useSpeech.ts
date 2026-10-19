import * as Speech from "expo-speech";
import { useCallback, useEffect, useRef } from "react";

import { SpeechSpeed, useSettings } from "@/context/SettingsContext";

// Speech rate for each setting. Tune these on a real device.
const SPEED_RATES: Record<SpeechSpeed, number> = {
  slow: 0.75,
  normal: 1,
  fast: 1.3,
};

type SpeakOptions = {
  // Overrides the saved voice (used by the voice picker preview).
  // null means "Phone default".
  voiceId?: string | null;
};

// Speaks text with the saved voice and speed. `speak` and `stop` keep the
// same identity between renders, so they are safe in useEffect dependencies.
export function useSpeech() {
  const { speechSpeed, voice } = useSettings();
  const latest = useRef({ speechSpeed, voice });

  useEffect(() => {
    latest.current = { speechSpeed, voice };
  }, [speechSpeed, voice]);

  const speak = useCallback((text: string, options?: SpeakOptions) => {
    const { speechSpeed: speed, voice: saved } = latest.current;
    const voiceId =
      options && "voiceId" in options ? options.voiceId : saved?.id;

    Speech.stop();
    Speech.speak(text, {
      rate: SPEED_RATES[speed],
      voice: voiceId ?? undefined,
    });
  }, []);

  const stop = useCallback(() => {
    Speech.stop();
  }, []);

  return { speak, stop };
}