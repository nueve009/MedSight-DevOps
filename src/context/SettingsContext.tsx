import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

// ─── TYPES ───

export type SpeechSpeed = "slow" | "normal" | "fast";

// null means "Phone default".
export type SelectedVoice = {
  id: string;
  label: string;
};

type SettingsValue = {
  speechSpeed: SpeechSpeed;
  setSpeechSpeed: (value: SpeechSpeed) => void;
  vibration: boolean;
  setVibration: (value: boolean) => void;
  highContrast: boolean;
  setHighContrast: (value: boolean) => void;
  voice: SelectedVoice | null;
  setVoice: (value: SelectedVoice | null) => void;
};

// ─── CONTEXT ───

const SettingsContext = createContext<SettingsValue | null>(null);

// Settings live in memory only for now, so they reset when the app restarts.
// Persisting them is still to do.
export function SettingsProvider({ children }: { children: ReactNode }) {
  const [speechSpeed, setSpeechSpeed] = useState<SpeechSpeed>("normal");
  const [vibration, setVibration] = useState(true);
  const [highContrast, setHighContrast] = useState(true);
  const [voice, setVoice] = useState<SelectedVoice | null>(null);

  const value = useMemo(
    () => ({
      speechSpeed,
      setSpeechSpeed,
      vibration,
      setVibration,
      highContrast,
      setHighContrast,
      voice,
      setVoice,
    }),
    [speechSpeed, vibration, highContrast, voice],
  );

  return (
    <SettingsContext.Provider value={value}>
      {children}
    </SettingsContext.Provider>
  );
}

export function useSettings(): SettingsValue {
  const context = useContext(SettingsContext);
  if (!context) {
    throw new Error("useSettings must be used inside <SettingsProvider>");
  }
  return context;
}