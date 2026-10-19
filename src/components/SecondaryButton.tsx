import * as Haptics from "expo-haptics";
import { Pressable, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Icon, IconName } from "@/components/Icon";
import { colors, radii, sizes, spacing } from "@/theme/theme";

type SecondaryButtonProps = {
  label: string;
  onPress: () => void;
  icon?: IconName;
  accessibilityLabel?: string;
  accessibilityHint?: string;
};

export function SecondaryButton({
  label,
  onPress,
  icon,
  accessibilityLabel,
  accessibilityHint,
}: SecondaryButtonProps) {
  function handlePress() {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    onPress();
  }

  return (
    <Pressable
      onPress={handlePress}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityHint={accessibilityHint}
      style={({ pressed }) => [styles.button, pressed && styles.pressed]}
    >
      <View style={styles.content}>
        {icon ? <Icon name={icon} size={32} color={colors.text} /> : null}
        <AppText variant="title" bold>
          {label}
        </AppText>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    width: "100%",
    minHeight: sizes.secondaryButton,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.background,
    borderWidth: 3,
    borderColor: colors.text,
    borderRadius: radii.xl,
    paddingHorizontal: spacing.lg,
  },
  pressed: {
    backgroundColor: colors.surfaceRaised,
  },
  content: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
  },
});