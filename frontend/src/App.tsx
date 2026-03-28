import "./App.css";
import * as React from "react";
import { CssBaseline, ThemeProvider } from "@mui/material";
import type { PaletteMode } from "@mui/material";
import { BrowserRouter } from "react-router-dom";
import { createAppTheme } from "./theme";
import { Layout } from "./Layout";

const THEME_STORAGE_KEY = "mind-inbox-theme-mode";

function App() {
  const [themeMode, setThemeMode] = React.useState<PaletteMode>(() => {
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    return saved === "dark" ? "dark" : "light";
  });

  React.useEffect(() => {
    localStorage.setItem(THEME_STORAGE_KEY, themeMode);
  }, [themeMode]);

  const theme = React.useMemo(() => createAppTheme(themeMode), [themeMode]);

  const handleToggleTheme = React.useCallback(() => {
    setThemeMode((prev) => (prev === "light" ? "dark" : "light"));
  }, []);

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <BrowserRouter>
        <Layout themeMode={themeMode} onToggleTheme={handleToggleTheme} />
      </BrowserRouter>
    </ThemeProvider>
  );
}

export default App;
