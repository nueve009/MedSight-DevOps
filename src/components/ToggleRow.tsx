import { Pressable, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { colors, radii, sizes, spacing } from "@/theme/theme";

type ToggleRowProps = {
  label: string;
  value: boolean;
  onValueChange: (value: boolean) => void;
  stateLabel: string;
  accessibilityHint?: string;
};

const TRACK_WIDTH = 76;
const TRACK_HEIGHT = 44;
const KNOB_SIZE = 28;

export function ToggleRow({
  label,
  value,
  onValueChange,
  stateLabel,
  accessibilityHint,
}: ToggleRowProps) {
  return (
    <Pressable
      onPress={() => onValueChange(!value)}
      accessibilityRole="switch"
      accessibilityLabel={label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ checked: value }}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      <AppText variant="title" bold style={styles.label}>
        {label}
      </AppText>
      <View style={styles.trailing}>
        <AppText variant="body" bold>
          {stateLabel}
        </AppText>
        <View
          style={[
            styles.track,
            value ? styles.trackOn : styles.trackOff,
          ]}
        >
          <View
            style={[
              styles.knob,
              value ? styles.knobOn : styles.knobOff,
            ]}
          />
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    width: "100%",
    minHeight: sizes.secondaryButton,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: colors.border,
    borderRadius: radii.lg,
  },
  pressed: {
    backgroundColor: colors.surfaceRaised,
  },
  label: {
    flexShrink: 1,
  },
  trailing: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
  },
  track: {
    width: TRACK_WIDTH,
    height: TRACK_HEIGHT,
    justifyContent: "center",
    paddingHorizontal: spacing.xs / 2,
    borderWidth: 3,
    borderRadius: TRACK_HEIGHT / 2,
  },
  trackOn: {
    alignItems: "flex-end",
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  trackOff: {
    alignItems: "flex-start",
    backgroundColor: colors.background,
    borderColor: colors.borderStrong,
  },
  knob: {
    width: KNOB_SIZE,
    height: KNOB_SIZE,
    borderRadius: KNOB_SIZE / 2,
  },
  knobOn: {
    backgroundColor: colors.onAccent,
  },
  knobOff: {
    backgroundColor: colors.textMuted,
  },
});