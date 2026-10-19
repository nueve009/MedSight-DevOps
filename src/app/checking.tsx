import { router, useLocalSearchParams } from "expo-router";
import { useEffect } from "react";
import { StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { SecondaryButton } from "@/components/SecondaryButton";
import { messages } from "@/constants/messages";
import { useSpeech } from "@/hooks/useSpeech";
import { ApiError, identifyMedicine } from "@/services/api";
import { addHistory, findMyMedicine } from "@/services/db";
import { colors, spacing } from "@/theme/theme";

const OUTER_RING_SIZE = 248;
const INNER_RING_SIZE = 180;
const CORE_SIZE = 112;
const CORE_ICON_SIZE = 56;

export default function CheckingScreen() {
  // The text read from the medicine box, sent by the Scan screen.
  const params = useLocalSearchParams<{ text?: string | string[] }>();
  const text = Array.isArray(params.text) ? params.text[0] : params.text;
  const { speak, stop } = useSpeech();

  // When the screen opens: say "Checking", ask the backend, then replace
  // this screen with the result. Leaving the screen (Cancel or the back
  // gesture) cancels the request.
  useEffect(() => {
    const controller = new AbortController();
    speak(messages.checking.spoken);

    identifyMedicine(text ?? "", controller.signal)
      .then(async (result) => {
        // Remember this scan, and see if a caregiver saved this medicine.
        // A problem with the phone's storage must never hide the result.
        let saved = null;
        try {
          await addHistory(result);
          saved = result.name ? await findMyMedicine(result.name) : null;
        } catch {
          saved = null;
        }
        if (controller.signal.aborted) {
          return;
        }

        router.replace({
          pathname: "/result",
          params: {
            status: result.status,
            name: result.name ?? "",
            strength: result.strength ?? "",
            onList: saved ? "true" : "false",
            doseNote: saved?.doseNote ?? "",
            spokenText: result.spokenText,
          },
        });
      })
      .catch((error: unknown) => {
        const kind = error instanceof ApiError ? error.kind : "server";
        if (kind === "cancelled") {
          return;
        }
        if (kind === "network") {
          router.replace("/offline");
          return;
        }
        // Timeout or server error. Pass on the backend's own sentence when
        // it sent one (for example "the service is busy").
        router.replace({
          pathname: "/offline",
          params: {
            reason: "server",
            spokenText:
              error instanceof ApiError ? (error.spokenText ?? "") : "",
          },
        });
      });

    return () => {
      controller.abort();
      stop();
    };
  }, [text, speak, stop]);

  return (
    <Screen>
      <View style={styles.content}>
        <View
          accessible
          accessibilityRole="progressbar"
          accessibilityLabel={messages.checking.indicatorLabel}
          style={styles.outerRing}
        >
          <View style={styles.innerRing}>
            <View style={styles.core}>
              <Icon
                name="speaker"
                size={CORE_ICON_SIZE}
                color={colors.onAccent}
              />
            </View>
          </View>
        </View>

        <View accessible accessibilityLiveRegion="polite" style={styles.text}>
          <AppText variant="h1" bold style={styles.centered}>
            {messages.checking.title}
          </AppText>
          <AppText
            variant="body"
            color={colors.textMuted}
            style={styles.centered}
          >
            {messages.checking.body}
          </AppText>
        </View>
      </View>

      <SecondaryButton
        label={messages.checking.cancel}
        onPress={() => router.back()}
        accessibilityHint={messages.checking.cancelHint}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.xl,
  },
  outerRing: {
    width: OUTER_RING_SIZE,
    height: OUTER_RING_SIZE,
    borderRadius: OUTER_RING_SIZE / 2,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 3,
    borderStyle: "dashed",
    borderColor: colors.borderStrong,
  },
  innerRing: {
    width: INNER_RING_SIZE,
    height: INNER_RING_SIZE,
    borderRadius: INNER_RING_SIZE / 2,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 4,
    borderColor: colors.textMuted,
    opacity: 0.6,
  },
  core: {
    width: CORE_SIZE,
    height: CORE_SIZE,
    borderRadius: CORE_SIZE / 2,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.accent,
  },
  text: {
    alignItems: "center",
    gap: spacing.sm,
  },
  centered: {
    textAlign: "center",
  },
});