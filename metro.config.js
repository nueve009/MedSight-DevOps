// Learn more https://docs.expo.dev/guides/customizing-metro/
const path = require("path");
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);

// The Python backend (and its .venv, with thousands of files) lives in
// backend/. It is not part of the app, so Metro should not scan or bundle it.
const backendFolder = path.resolve(__dirname, "backend");
const escapeForRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const backendPattern = new RegExp(`^${escapeForRegExp(backendFolder)}[\\\\/]`);

const existing = config.resolver.blockList;
config.resolver.blockList = [
  ...(Array.isArray(existing) ? existing : existing ? [existing] : []),
  backendPattern,
];

module.exports = config;
