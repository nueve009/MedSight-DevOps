import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { ScrollView, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { CornerButton } from "@/components/CornerButton";
import { ListRow } from "@/components/ListRow";
import { Screen } from "@/components/Screen";
import { SegmentedControl } from "@/components/SegmentedControl";
import { ToggleRow } from "@/components/ToggleRow";
import { messages } from "@/constants/messages";
import { SpeechSpeed, useSettings } from "@/context/SettingsContext";
import { countMyMedicines } from "@/services/db";
import { colors, spacing } from "@/theme/theme";

const SPEED_OPTIONS: { label: string; value: SpeechSpeed }[] = [
  { label: messages.settings.speeds.slow, value: "slow" },
  { label: messages.settings.speeds.normal, value: "normal" },
  { label: messages.settings.speeds.fast, value: "fast" },
];

export default function SettingsScreen() {
  const {
    speechSpeed,
    setSpeechSpeed,
    vibration,
    setVibration,
    highContrast,
    setHighContrast,
    voice,
  } = useSettings();

  // How many medicines are saved. Counted again each time this screen comes
  // to the front, so it is right after returning from My medicines.
  const [savedCount, setSavedCount] = useState(0);
  useFocusEffect(
    useCallback(() => {
      let active = true;
      countMyMedicines()
        .then((count) => {
          if (active) {
            setSavedCount(count);
          }
        })
        .catch(() => {});
      return () => {
        active = false;
      };
    }, []),
  );

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
          {messages.settings.title}
        </AppText>
      </View>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.scrollContent}
      >
        <View style={styles.group}>
          <AppText variant="body" bold>
            {messages.settings.speechSpeed}
          </AppText>
          <SegmentedControl
            label={messages.settings.speechSpeed}
            options={SPEED_OPTIONS}
            value={speechSpeed}
            onChange={setSpeechSpeed}
          />
        </View>

        <ToggleRow
          label={messages.settings.vibration}
          value={vibration}
          onValueChange={setVibration}
          stateLabel={vibration ? messages.settings.on : messages.settings.off}
          accessibilityHint={messages.settings.vibrationHint}
        />

        <ToggleRow
          label={messages.settings.highContrast}
          value={highContrast}
          onValueChange={setHighContrast}
          stateLabel={
            highContrast ? messages.settings.on : messages.settings.off
          }
          accessibilityHint={messages.settings.highContrastHint}
        />

        <ListRow
          label={messages.settings.voice}
          value={voice?.label ?? messages.settings.voiceDefault}
          onPress={() => router.push("/voice")}
          accessibilityHint={messages.settings.voiceHint}
        />

        <View style={styles.divider} />

        <View style={styles.group}>
          <AppText
            variant="bodySm"
            bold
            color={colors.textMuted}
            accessibilityRole="header"
            style={styles.sectionLabel}
          >
            {messages.settings.caregiversHeading}
          </AppText>

          <ListRow
            label={messages.settings.myMedicines}
            value={messages.settings.myMedicinesSaved(savedCount)}
            onPress={() => router.push("/my-medicines")}
            accessibilityHint={messages.settings.myMedicinesHint}
          />

          <AppText variant="bodySm" color={colors.textMuted}>
            {messages.settings.myMedicinesHelp}
          </AppText>
        </View>
      </ScrollView>
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
  // Lets a long title wrap instead of running off the screen.
  title: {
    flexShrink: 1,
  },
  scroll: {
    flex: 1,
  },
  scrollContent: {
    gap: spacing.sm,
    paddingBottom: spacing.lg,
  },
  group: {
    gap: spacing.xs,
  },
  divider: {
    height: 3,
    marginVertical: spacing.sm,
    backgroundColor: colors.border,
  },
  sectionLabel: {
    letterSpacing: 2,
    textTransform: "uppercase",
  },
});