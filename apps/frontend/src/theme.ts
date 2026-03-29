// theme.ts
import { createTheme, alpha } from "@mui/material/styles";
import type { PaletteMode } from "@mui/material";

const neutral = {
  0: "#FFFFFF",
  50: "#F4F7FB",
  100: "#EDF2FA",
  200: "#D6E0EF",
  300: "#BAC8DB",
  400: "#91A2BC",
  500: "#70829F",
  600: "#5D6B82",
  700: "#445168",
  800: "#2E3D55",
  900: "#24324A",
};
const indigo = {
  50: "#EEF0FF",
  100: "#DCE1FF",
  200: "#C1CAFF",
  300: "#9CA8FF",
  400: "#7482FF",
  500: "#4653FF",
  600: "#3341F5",
  700: "#2B35CB",
  800: "#252FA3",
  900: "#20287F",
};
const cyan = {
  50: "#EAFBFF",
  100: "#D4F6FF",
  200: "#AEEFFF",
  300: "#7BE3FA",
  400: "#38CFE7",
  500: "#00A6C7",
  600: "#008BAC",
  700: "#007089",
  800: "#07596D",
  900: "#0C4658",
};
const violet = {
  100: "#F0E7FF",
  300: "#C7A4FF",
  500: "#7C3AED",
  700: "#6429C3",
};

export function createAppTheme(mode: PaletteMode) {
  const isDark = mode === "dark";

  return createTheme({
    palette: {
      mode,
      primary: {
        main: indigo[500],
        light: indigo[400],
        dark: indigo[700],
        contrastText: "#fff",
      },
      secondary: {
        main: cyan[500],
        light: cyan[300],
        dark: cyan[700],
        contrastText: "#fff",
      },
      info: {
        main: violet[500],
        light: violet[300],
        dark: violet[700],
        contrastText: "#fff",
      },
      background: isDark
        ? { default: "#0D1220", paper: "#141B2D" }
        : { default: neutral[50], paper: neutral[0] },
      text: isDark
        ? { primary: "#E7ECF8", secondary: "#A9B5CC" }
        : { primary: neutral[900], secondary: neutral[600] },
      divider: isDark ? alpha("#B9C4D8", 0.18) : neutral[200],
    },
    typography: {
      fontFamily:
        '"Inter", "BIZ UDPGothic", "Noto Sans JP", system-ui, -apple-system, sans-serif',
    },
    shape: { borderRadius: 14 },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            background: isDark
              ? "radial-gradient(circle at top right, rgba(117,130,255,0.22) 0%, rgba(117,130,255,0) 34%), radial-gradient(circle at left bottom, rgba(56,207,231,0.16) 0%, rgba(56,207,231,0) 30%), linear-gradient(180deg, #0b1020 0%, #11192b 100%)"
              : "radial-gradient(circle at top right, rgba(70,83,255,0.12) 0%, rgba(70,83,255,0) 28%), radial-gradient(circle at left bottom, rgba(0,166,199,0.10) 0%, rgba(0,166,199,0) 24%), linear-gradient(180deg, #fbfdff 0%, #f4f7fb 100%)",
            color: isDark ? "#E7ECF8" : neutral[900],
          },
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: {
            border: isDark
              ? `1px solid ${alpha("#C5D2EA", 0.18)}`
              : `1px solid ${neutral[200]}`,
            boxShadow: isDark
              ? "0 24px 50px rgba(3, 7, 18, 0.48)"
              : "0 18px 44px rgba(36, 50, 74, 0.08)",
          },
        },
      },
      MuiAppBar: {
        styleOverrides: {
          root: {
            backdropFilter: "blur(10px)",
            backgroundColor: isDark
              ? alpha("#141B2D", 0.82)
              : alpha(neutral[0], 0.86),
          },
        },
      },
      MuiButton: {
        styleOverrides: {
          root: {
            borderRadius: 12,
            textTransform: "none",
            fontWeight: 700,
          },
          containedPrimary: {
            boxShadow: "0 10px 24px rgba(70, 83, 255, 0.24)",
            background: `linear-gradient(135deg, ${indigo[500]} 0%, ${violet[500]} 100%)`,
            "&:hover": {
              background: `linear-gradient(135deg, ${indigo[600]} 0%, ${violet[700]} 100%)`,
            },
          },
        },
      },
      MuiListItemButton: {
        styleOverrides: {
          root: {
            borderRadius: 10,
            "&.Mui-selected": {
              backgroundColor: alpha(indigo[500], 0.12),
              "&:hover": { backgroundColor: alpha(indigo[500], 0.16) },
            },
          },
        },
      },
    },
  });
}
