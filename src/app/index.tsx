import { AppText } from "@/components/AppText";
import { CornerButton } from "@/components/CornerButton";
import { GuidancePill } from "@/components/GuidancePill";
import { Icon } from "@/components/Icon";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScanFrame } from "@/components/ScanFrame";
import { Screen } from "@/components/Screen";
import { messages } from "@/constants/messages";
import { useSpeech } from "@/hooks/useSpeech";
import { readLabelText } from "@/services/ocr";
import { colors, radii, spacing } from "@/theme/theme";
import NetInfo from "@react-native-community/netinfo";
import { CameraView, useCameraPermissions } from "expo-camera";
import { router, useIsFocused } from "expo-router";
import { useEffect } from "react";
import { Linking, StyleSheet, View } from "react-native";
// import { Pressable, StyleSheet, Text, View } from "react-native"; //for testing only (REPLACEMENT FOR ABOVE ROW); delete/comment out before commit

export default function ScanScreen() {
  // permission is null for a moment while the phone looks it up.
  const [permission, requestPermission] = useCameraPermissions();
  const cameraAllowed = permission?.granted === true;
  const cameraRefused = permission !== null && !permission.granted;
  // The camera only runs while this screen is the one on top.
  const isFocused = useIsFocused();
  const { speak, stop } = useSpeech();

  // Tell the user out loud why there is no camera yet.
  useEffect(() => {
    if (cameraRefused && isFocused) {
      speak(messages.scan.cameraNeededSpoken);
      return stop;
    }
  }, [cameraRefused, isFocused, speak, stop]);

  async function handleAllowCamera() {
    // After the question was refused, Android stops showing it. The only
    // way left is the app's page in the phone settings.
    if (permission && !permission.canAskAgain) {
      await Linking.openSettings();
      return;
    }
    const answer = await requestPermission();
    // Refused for good just now: take the user to the settings page, so the
    // button never looks like it does nothing.
    if (!answer.granted && !answer.canAskAgain) {
      await Linking.openSettings();
    }
  }

  async function handleScan() {
    // No internet: go straight to the Offline screen instead of waiting.
    const state = await NetInfo.fetch();
    const online =
      state.isConnected === true && state.isInternetReachable !== false;
    if (!online) {
      router.push("/offline");
      return;
    }

    // TODO: take the photo and run real OCR (see src/services/ocr.ts).
    const text = await readLabelText();
    router.push({ pathname: "/checking", params: { text } });
  }

  return (
    <Screen>
      <View style={styles.topRow}>
        <CornerButton
          label={messages.scan.history}
          icon="history"
          onPress={() => router.push("/history")}
          accessibilityHint={messages.scan.historyHint}
        />
        <CornerButton
          label={messages.scan.settings}
          icon="settings"
          onPress={() => router.push("/settings")}
          accessibilityHint={messages.scan.settingsHint}
        />
      </View>
      
      {/* {__DEV__ ? ( // FOR TESTING ONLY. NO IMPACT BUT DELETE BEFORE FINAL BUILD. shows "checking", "verified", "not verified", "offline" screens.
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
          {[
            { label: "Checking", go: () => router.push("/checking") },
            {
              label: "Verified",
              go: () =>
                router.push({
                  pathname: "/result",
                  params: {
                    status: "verified",
                    name: "Paracetamol",
                    strength: "500 mg",
                    form: "Tablet",
                    onList: "true",
                    doseNote: "Take one tablet after breakfast.",
                  },
                }),
            },
            {
              label: "Not verified",
              go: () =>
                router.push({ pathname: "/result", params: { status: "notVerified" } }),
            },
            { label: "Offline", go: () => router.push("/offline") },
          ].map((item) => (
            <Pressable
              key={item.label}
              onPress={item.go}
              style={{ padding: 8, backgroundColor: "#333", borderRadius: 8 }}
            >
              <Text style={{ color: "#fff", fontSize: 18 }}>{item.label}</Text>
            </Pressable>
          ))}
        </View>
      ) : null} */}

      <View style={styles.camera}>
        {/* The live camera fills the card. CameraView can't have children,
            so the text, frame and hint are drawn on top of it. */}
        {cameraAllowed && isFocused ? (
          <CameraView style={StyleSheet.absoluteFill} facing="back" />
        ) : null}

        {cameraRefused ? (
          <View style={styles.permission}>
            <Icon name="camera" size={64} color={colors.accent} />
            <AppText
              variant="title"
              bold
              accessibilityRole="header"
              style={styles.centered}
            >
              {messages.scan.cameraNeededTitle}
            </AppText>
            <AppText
              variant="body"
              color={colors.textMuted}
              style={styles.centered}
            >
              {messages.scan.cameraNeededBody}
            </AppText>
          </View>
        ) : (
          <>
            {/* Black backing keeps the text readable over a bright picture. */}
            <View style={styles.instructionBox}>
              <AppText variant="body" bold style={styles.centered}>
                {messages.scan.instruction}
              </AppText>
            </View>
            <View style={styles.frameArea}>
              <ScanFrame />
            </View>
            <GuidancePill text={messages.scan.hint} />
          </>
        )}
      </View>

      {cameraRefused ? (
        <PrimaryButton
          label={messages.scan.allowCamera}
          icon="camera"
          onPress={handleAllowCamera}
          accessibilityHint={messages.scan.allowCameraHint}
        />
      ) : (
        <PrimaryButton
          label={messages.scan.scanButton}
          icon="camera"
          onPress={handleScan}
          accessibilityHint={messages.scan.scanButtonHint}
        />
      )}
    </Screen>
  );
}

// ─── STYLES ───

const styles = StyleSheet.create({
  topRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: spacing.md,
  },
  camera: {
    flex: 1,
    marginVertical: spacing.md,
    padding: spacing.md,
    gap: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radii.xl,
    overflow: "hidden",
  },
  instructionBox: {
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.sm,
    backgroundColor: colors.background,
    borderRadius: radii.sm,
  },
  centered: {
    textAlign: "center",
  },
  permission: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.md,
  },
  frameArea: {
    flex: 1,
    paddingHorizontal: spacing.md,
  },
});