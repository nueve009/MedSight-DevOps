import { StyleSheet, TextInput, View } from "react-native";

import { AppText } from "@/components/AppText";
import { colors, fonts, fontSizes, radii, sizes, spacing } from "@/theme/theme";

// ─── PROPS ───

type TextFieldProps = {
  label: string;
  value: string;
  onChangeText: (text: string) => void;
  // Small muted text under the label, e.g. "Optional".
  helper?: string;
  accessibilityHint?: string;
  // Called when the user presses the keyboard's Done / Next key.
  onSubmitEditing?: () => void;
};

// ─── COMPONENT ───

// A labelled one-line text box. The label is also read by screen readers.
export function TextField({
  label,
  value,
  onChangeText,
  helper,
  accessibilityHint,
  onSubmitEditing,
}: TextFieldProps) {
  return (
    <View style={styles.field}>
      <AppText variant="bodySm" bold>
        {label}
      </AppText>
      {helper ? (
        <AppText variant="caption" color={colors.textMuted}>
          {helper}
        </AppText>
      ) : null}
      <TextInput
        value={value}
        onChangeText={onChangeText}
        onSubmitEditing={onSubmitEditing}
        accessibilityLabel={label}
        accessibilityHint={accessibilityHint}
        autoCorrect={false}
        cursorColor={colors.accent}
        selectionColor={colors.accent}
        style={styles.input}
      />
    </View>
  );
}

// ─── STYLES ───

const styles = StyleSheet.create({
  field: {
    gap: spacing.xs / 2,
  },
  input: {
    minHeight: sizes.cornerButton,
    marginTop: spacing.xs / 2,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    color: colors.text,
    fontFamily: fonts.regular,
    fontSize: fontSizes.body,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: colors.borderStrong,
    borderRadius: radii.sm,
  },
});
