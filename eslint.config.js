// https://docs.expo.dev/guides/using-eslint/
const { defineConfig } = require('eslint/config');
const expoConfig = require("eslint-config-expo/flat");

module.exports = defineConfig([
  expoConfig,
  {
    // The Python backend has its own tools; ESLint only checks the app.
    ignores: ["dist/*", "backend/*"],
  }
]);
