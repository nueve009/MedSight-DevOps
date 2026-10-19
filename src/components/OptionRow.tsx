import { Pressable, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { colors, radii, sizes, spacing } from "@/theme/theme";

type OptionRowProps = {
  label: string;
  description?: string;
  selected: boolean;
  onPress: () => void;
  accessibilityHint?: string;
};

const CHECK_SIZE = 36;

export function OptionRow({
  label,
  description,
  selected,
  onPress,
  accessibilityHint,
}: OptionRowProps) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="radio"
      accessibilityLabel={description ? `${label}, ${description}` : label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ checked: selected }}
      style={({ pressed }) => [
        styles.row,
        selected ? styles.rowSelected : styles.rowIdle,
        pressed && styles.pressed,
      ]}
    >
      <View style={styles.text}>
        <AppText variant="title" bold>
          {label}
        </AppText>
        {description ? (
          <AppText variant="bodySm" color={colors.textMuted}>
            {description}
          </AppText>
        ) : null}
      </View>
      {/* The slot is always reserved so rows don't shift when selected.
          Selection shows as a check icon and a yellow border, not color alone. */}
      <View style={styles.check}>
        {selected ? (
          <Icon name="check" size={CHECK_SIZE} color={colors.accent} />
        ) : null}
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
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderRadius: radii.lg,
  },
  rowIdle: {
    borderColor: colors.border,
  },
  rowSelected: {
    borderColor: colors.accent,
  },
  pressed: {
    backgroundColor: colors.surfaceRaised,
  },
  text: {
    flex: 1,
    gap: spacing.xs / 2,
  },
  check: {
    width: CHECK_SIZE,
    height: CHECK_SIZE,
    alignItems: "center",
    justifyContent: "center",
  },
});