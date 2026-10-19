import * as Haptics from "expo-haptics";
import { Pressable, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { colors, radii, sizes, spacing } from "@/theme/theme";

// ─── PROPS ───

type MedicineRowProps = {
  name: string;
  doseNote?: string;
  // Text and screen-reader wording for the remove button.
  removeLabel: string;
  removeAccessibilityLabel: string;
  removeAccessibilityHint?: string;
  onRemove: () => void;
};

// ─── COMPONENT ───

// One saved medicine: name, optional dose note, and a Remove button.
export function MedicineRow({
  name,
  doseNote,
  removeLabel,
  removeAccessibilityLabel,
  removeAccessibilityHint,
  onRemove,
}: MedicineRowProps) {
  function handleRemove() {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    onRemove();
  }

  return (
    <View style={styles.row}>
      <View
        accessible
        accessibilityRole="text"
        accessibilityLabel={doseNote ? `${name}. ${doseNote}` : name}
        style={styles.text}
      >
        <AppText variant="title" bold>
          {name}
        </AppText>
        {doseNote ? (
          <AppText variant="bodySm" color={colors.textMuted}>
            {doseNote}
          </AppText>
        ) : null}
      </View>
      <Pressable
        onPress={handleRemove}
        accessibilityRole="button"
        accessibilityLabel={removeAccessibilityLabel}
        accessibilityHint={removeAccessibilityHint}
        style={({ pressed }) => [styles.remove, pressed && styles.pressed]}
      >
        <AppText variant="bodySm" bold>
          {removeLabel}
        </AppText>
      </Pressable>
    </View>
  );
}

// ─── STYLES ───

const styles = StyleSheet.create({
  row: {
    width: "100%",
    minHeight: sizes.secondaryButton,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingLeft: spacing.lg,
    paddingRight: spacing.sm,
    paddingVertical: spacing.sm,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: colors.border,
    borderRadius: radii.lg,
  },
  text: {
    flex: 1,
    gap: spacing.xs / 2,
  },
  remove: {
    minHeight: sizes.cornerButton,
    justifyContent: "center",
    paddingHorizontal: spacing.md,
    backgroundColor: colors.surfaceRaised,
    borderWidth: 2,
    borderColor: colors.borderStrong,
    borderRadius: radii.sm,
  },
  pressed: {
    opacity: 0.8,
  },
});
