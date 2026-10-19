import { colors, fonts, fontSizes } from "@/theme/theme";
import type { ReactNode } from "react";
import { StyleProp, Text, TextProps, TextStyle } from "react-native";

// ─── PROPS ───

// Extra Text props (accessibilityRole, numberOfLines, ...) are passed through.
type AppTextProps = Omit<TextProps, "style" | "children"> & {
  variant: "display" | "h1" | "title" | "body" | "bodySm" | "caption";
  children: ReactNode;
  bold?: boolean;
  color?: string;
  style?: StyleProp<TextStyle>;
};

// ─── COMPONENTS ───

export function AppText({
  variant,
  children,
  bold = false,
  color = colors.text,
  style,
  ...textProps
}: AppTextProps) {
  return (
    <Text
      {...textProps}
      style={[
        {
          fontFamily: bold ? fonts.bold : fonts.regular,
          fontSize: fontSizes[variant],
          color,
        },
        style,
      ]}
    >
      {children}
    </Text>
  );
}