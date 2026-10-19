import { router } from "expo-router";
import * as Speech from "expo-speech";
import { useEffect, useMemo, useState } from "react";
import { FlatList, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { CornerButton } from "@/components/CornerButton";
import { OptionRow } from "@/components/OptionRow";
import { Screen } from "@/components/Screen";
import { messages } from "@/constants/messages";
import { SelectedVoice, useSettings } from "@/context/SettingsContext";
import { useSpeech } from "@/hooks/useSpeech";
import { colors, spacing } from "@/theme/theme";

type VoiceOption = {
  key: string;
  label: string;
  description?: string;
  voice: SelectedVoice | null; // null = Phone default
};

const DEFAULT_OPTION: VoiceOption = {
  key: "default",
  label: messages.voice.phoneDefault,
  voice: null,
};

// Keeps English voices only (the app's text is English), best quality first.
function buildOptions(voices: Speech.Voice[]): VoiceOption[] {
  const english = voices
    .filter((item) => item.language.toLowerCase().startsWith("en"))
    .sort((a, b) => {
      const rankA = a.quality === Speech.VoiceQuality.Enhanced ? 0 : 1;
      const rankB = b.quality === Speech.VoiceQuality.Enhanced ? 0 : 1;
      return rankA - rankB || a.name.localeCompare(b.name);
    });

  const counts: Record<string, number> = {};

  return english.map((item) => {
    const tag = item.language.replace("_", "-");
    const region = messages.voice.regions[tag] ?? tag;
    const enhanced = item.quality === Speech.VoiceQuality.Enhanced;
    // iOS gives real names ("Samantha"). Android gives codes such as
    // "en-us-x-sfg#male_1-local", so those get a readable numbered name.
    const isCode = /-x-|#/.test(item.name);

    let label = item.name;
    const parts: string[] = [];

    if (isCode) {
      counts[tag] = (counts[tag] ?? 0) + 1;
      label = messages.voice.genericName(region, counts[tag]);
    } else {
      parts.push(region);
    }
    if (enhanced) {
      parts.push(messages.voice.enhanced);
    }

    return {
      key: item.identifier,
      label,
      description: parts.length > 0 ? parts.join(" · ") : undefined,
      voice: { id: item.identifier, label },
    };
  });
}

export default function VoiceScreen() {
  const { voice, setVoice } = useSettings();
  const { speak, stop } = useSpeech();
  const [voices, setVoices] = useState<Speech.Voice[] | null>(null);

  useEffect(() => {
    let active = true;

    Speech.getAvailableVoicesAsync()
      .then((list) => {
        if (active) setVoices(list);
      })
      .catch(() => {
        if (active) setVoices([]);
      });

    return () => {
      active = false;
      stop();
    };
  }, [stop]);

  const options = useMemo(
    () => (voices ? buildOptions(voices) : []),
    [voices],
  );
  const data = useMemo(() => [DEFAULT_OPTION, ...options], [options]);
  const selectedId = voice?.id ?? null;

  function choose(next: SelectedVoice | null) {
    setVoice(next);
    // Play a short sample so the user hears the voice they just picked.
    speak(messages.voice.sample, { voiceId: next?.id ?? null });
  }

  return (
    <Screen>
      <View style={styles.header}>
        <CornerButton
          label={messages.common.back}
          icon="back"
          onPress={() => router.back()}
          accessibilityHint={messages.common.backHint}
        />
        <AppText
          variant="h1"
          bold
          accessibilityRole="header"
          style={styles.title}
        >
          {messages.voice.title}
        </AppText>
      </View>

      <FlatList
        data={data}
        keyExtractor={(item) => item.key}
        accessibilityRole="radiogroup"
        accessibilityLabel={messages.voice.listLabel}
        style={styles.list}
        contentContainerStyle={styles.listContent}
        renderItem={({ item }) => (
          <OptionRow
            label={item.label}
            description={item.description}
            selected={(item.voice?.id ?? null) === selectedId}
            onPress={() => choose(item.voice)}
            accessibilityHint={messages.voice.selectHint}
          />
        )}
        ListFooterComponent={
          voices === null ? (
            <View accessible accessibilityLiveRegion="polite">
              <AppText variant="body" color={colors.textMuted}>
                {messages.voice.loading}
              </AppText>
            </View>
          ) : options.length === 0 ? (
            <View accessible>
              <AppText variant="body" color={colors.textMuted}>
                {messages.voice.noOtherVoices}
              </AppText>
            </View>
          ) : null
        }
      />

      <View accessible style={styles.footer}>
        <AppText variant="bodySm" color={colors.textMuted}>
          {messages.voice.help}
        </AppText>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingBottom: spacing.md,
  },
  title: {
    flexShrink: 1,
  },
  list: {
    flex: 1,
  },
  listContent: {
    gap: spacing.sm,
    paddingBottom: spacing.lg,
  },
  footer: {
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
  },
});