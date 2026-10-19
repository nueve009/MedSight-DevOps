import { colors } from "@/theme/theme";
import Svg, { Path } from "react-native-svg";
import { IconName, iconPaths } from "./iconPaths";

export type { IconName };

// ─── PROPS ───

type IconProps = {
  name: IconName;
  size?: number;
  color?: string;
};

// ─── COMPONENT ───

export function Icon({ name, size = 32, color = colors.text }: IconProps) {
  return (
    <Svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      accessible={false}
      importantForAccessibility="no-hide-descendants"
    >
      {iconPaths[name].map((d) => (
        <Path key={d} d={d} />
      ))}
    </Svg>
  );
}
