import NetInfo from "@react-native-community/netinfo";
import { router, useLocalSearchParams } from "expo-router";
import { useEffect } from "react";
import { StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { PrimaryButton } from "@/components/PrimaryButton";
import { Screen } from "@/components/Screen";
import { SecondaryButton } from "@/components/SecondaryButton";
import { messages } from "@/constants/messages";
import { useSpeech } from "@/hooks/useSpeech";
import { colors, spacing } from "@/theme/theme";

const FRAME_SIZE = 200;
const ICON_SIZE = 96;

type ParamValue = string | string[] | undefined;

function first(value: ParamValue): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default function OfflineScreen() {
  // reason "server": the internet works but the backend failed or was too
  // slow. Same layout, different words and icon.
  const params = useLocalSearchParams<{
    reason?: ParamValue;
    spokenText?: ParamValue;
  }>();
  const isServerProblem = first(params.reason) === "server";
  const copy = isServerProblem ? messages.serverError : messages.offline;
  const spoken = first(params.spokenText) || copy.spoken;
  const { speak, stop } = useSpeech();

  useEffect(() => {
    speak(spoken);
    return stop;
  }, [spoken, speak, stop]);

  async function handleTryAgain() {
    stop();
    const state = await NetInfo.fetch();
    // isInternetReachable can be null while the phone is still checking,
    // so only an explicit false counts as "no internet".
    const online =
      state.isConnected === true && state.isInternetReachable !== false;

    if (online) {
      router.dismissAll();
      return;
    }

    // Still offline: say so again instead of staying silent.
    speak(messages.offline.spoken);
  }

  return (
    <Screen>
      <View style={styles.content}>
        <View style={styles.frame}>
          <Icon
            name={isServerProblem ? "warning" : "wifiOff"}
            size={ICON_SIZE}
            color={colors.accent}
          />
        </View>

        <View
          accessible
          accessibilityRole="header"
          accessibilityLabel={copy.spoken}
          accessibilityLiveRegion="polite"
          style={styles.text}
        >
          <AppText variant="h1" bold style={styles.centered}>
            {copy.title}
          </AppText>
          <AppText
            variant="body"
            color={colors.textMuted}
            style={styles.centered}
          >
            {copy.body}
          </AppText>
        </View>
      </View>

      <View style={styles.actions}>
        <SecondaryButton
          label={messages.offline.openHistory}
          onPress={() => router.push("/history")}
          accessibilityHint={messages.offline.openHistoryHint}
        />
        <PrimaryButton
          label={messages.offline.tryAgain}
          onPress={handleTryAgain}
          accessibilityHint={messages.offline.tryAgainHint}
        />
      </View>
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
  frame: {
    width: FRAME_SIZE,
    height: FRAME_SIZE,
    borderRadius: FRAME_SIZE / 2,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 6,
    borderColor: colors.accent,
  },
  text: {
    alignItems: "center",
    gap: spacing.sm,
  },
  centered: {
    textAlign: "center",
  },
  actions: {
    gap: spacing.sm,
    paddingTop: spacing.md,
  },
});