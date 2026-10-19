import { colors, radii, sizes, spacing } from "@/theme/theme";
import { StyleSheet, View } from "react-native";
import { AppText } from "./AppText";
import { Icon } from "./Icon";

// ─── PROPS ───

type GuidancePillProps = {
  text: string;
  // "pill": big hint over the camera (default).
  // "caption": small icon + text under the content (Result screen).
  variant?: "pill" | "caption";
};

// ─── COMPONENT ───

export function GuidancePill({ text, variant = "pill" }: GuidancePillProps) {
  if (variant === "caption") {
    // A visual hint for a touch gesture. Screen-reader users get the Repeat
    // button instead, so this is hidden from them to avoid a hint that
    // doesn't work with their gestures.
    return (
      <View
        style={styles.caption}
        accessible={false}
        accessibilityElementsHidden
        importantForAccessibility="no-hide-descendants"
      >
        <Icon name="speaker" size={24} color={colors.textMuted} />
        <AppText variant="bodySm" color={colors.textMuted} style={styles.text}>
          {text}
        </AppText>
      </View>
    );
  }

  // Big hint shown over the camera. When the text changes, screen readers
  // announce the new hint (accessibilityLiveRegion).
  return (
    <View
      style={styles.pill}
      accessible
      accessibilityLabel={text}
      accessibilityLiveRegion="polite"
    >
      <Icon name="speaker" size={36} color={colors.accent} />
      <AppText variant="title" bold style={styles.text}>
        {text}
      </AppText>
    </View>
  );
}

// ─── STYLES ───

const styles = StyleSheet.create({
  pill: {
    minHeight: sizes.cornerButton,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.xs,
    backgroundColor: colors.background,
    borderWidth: 2,
    borderColor: colors.accent,
    borderRadius: radii.md,
  },
  caption: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
  },
  text: {
    flexShrink: 1,
  },
});