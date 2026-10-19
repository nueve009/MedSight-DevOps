import { router, useLocalSearchParams } from "expo-router";
import { useEffect } from "react";
import { Pressable, ScrollView, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { GuidancePill } from "@/components/GuidancePill";
import { InfoCard } from "@/components/InfoCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { Screen } from "@/components/Screen";
import { SecondaryButton } from "@/components/SecondaryButton";
import { StatusBanner } from "@/components/StatusBanner";
import { messages } from "@/constants/messages";
import { useSpeech } from "@/hooks/useSpeech";
import { colors, spacing } from "@/theme/theme";

type ParamValue = string | string[] | undefined;

type ResultParams = {
  status?: ParamValue;
  name?: ParamValue;
  strength?: ParamValue;
  form?: ParamValue;
  onList?: ParamValue;
  doseNote?: ParamValue;
  // The backend's own sentence. When present it is spoken instead of ours,
  // because it carries the safety warnings.
  spokenText?: ParamValue;
};

function first(value: ParamValue): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default function ResultScreen() {
  const params = useLocalSearchParams<ResultParams>();
  const { speak, stop } = useSpeech();

  const name = first(params.name);
  const strength = first(params.strength);
  const form = first(params.form);
  const onList = first(params.onList) === "true";
  const doseNote = first(params.doseNote);
  const status = first(params.status);

  // Safety: show "Verified" only when the status says so AND the medicine
  // name and strength are known. Anything else falls back to a warning.
  // (The backend does not send the form, so it is optional.)
  const verified =
    status === "verified" && name && strength
      ? { name, strength, form: form || undefined }
      : null;

  // "unreadable": nothing usable was read. Otherwise: not verified.
  const warning =
    status === "unreadable"
      ? messages.result.unreadable
      : messages.result.notVerified;

  const spokenText =
    first(params.spokenText) ||
    (verified
      ? messages.result.verified.spoken({ ...verified, onList, doseNote })
      : warning.spoken);

  // Speak when the screen opens, stop when it closes.
  useEffect(() => {
    speak(spokenText);
    return stop;
  }, [spokenText, speak, stop]);

  function handleRepeat() {
    speak(spokenText);
  }

  function handleScanAgain() {
    stop();
    router.dismissAll();
  }

  return (
    <Screen>
      {/* Tap anywhere on the screen to hear the result again. Buttons inside
          still handle their own taps. This wrapper is hidden from screen
          readers on purpose: they use the Repeat button instead. */}
      <Pressable
        accessible={false}
        onPress={handleRepeat}
        style={styles.tapArea}
      >
        <View style={styles.banner}>
          <StatusBanner
            tone={verified ? "verified" : "warning"}
            label={
              verified ? messages.result.verified.banner : warning.banner
            }
          />
        </View>

        <ScrollView
          style={styles.scroll}
          contentContainerStyle={styles.scrollContent}
        >
          {verified ? (
            <View style={styles.content}>
              <View style={styles.heading}>
                <AppText variant="display" bold>
                  {verified.name}
                </AppText>
                <AppText variant="title" color={colors.textMuted}>
                  {messages.result.verified.details(
                    verified.strength,
                    verified.form,
                  )}
                </AppText>
              </View>

              {onList ? (
                <InfoCard
                  tone="verified"
                  label={messages.result.verified.onListLabel}
                  lines={doseNote ? [doseNote] : []}
                />
              ) : null}

              <GuidancePill
                variant="caption"
                text={messages.result.tapToRepeat}
              />
            </View>
          ) : (
            <View style={styles.content}>
              <View style={styles.heading}>
                <AppText variant="h1" bold>
                  {warning.headline}
                </AppText>
                <AppText variant="body" color={colors.textMuted}>
                  {warning.body}
                </AppText>
              </View>

              <InfoCard
                tone="warning"
                label={messages.result.notVerified.whatToDoLabel}
                lines={messages.result.notVerified.whatToDoLines}
              />
            </View>
          )}
        </ScrollView>

        <View style={styles.actions}>
          <SecondaryButton
            label={messages.result.repeat}
            icon="repeat"
            onPress={handleRepeat}
            accessibilityHint={messages.result.repeatHint}
          />
          <PrimaryButton
            label={
              verified
                ? messages.result.scanAgain
                : messages.result.notVerified.tryAgain
            }
            icon="camera"
            onPress={handleScanAgain}
            accessibilityHint={
              verified
                ? messages.result.scanAgainHint
                : messages.result.notVerified.tryAgainHint
            }
          />
        </View>
      </Pressable>
    </Screen>
  );
}

const styles = StyleSheet.create({
  tapArea: {
    flex: 1,
  },
  // Cancels Screen's horizontal padding so the banner runs edge to edge.
  banner: {
    marginHorizontal: -spacing.md,
  },
  scroll: {
    flex: 1,
  },
  scrollContent: {
    flexGrow: 1,
  },
  content: {
    gap: spacing.lg,
    paddingTop: spacing.lg,
    paddingHorizontal: spacing.xs,
  },
  heading: {
    gap: spacing.xs,
  },
  actions: {
    gap: spacing.sm,
    paddingTop: spacing.md,
  },
});