const { getDefaultConfig } = require("expo/metro-config");
const { withNativeWind } = require("nativewind/metro");

const config = getDefaultConfig(__dirname);
config.server = { ...config.server, rewriteRequestUrl: (url) => url };

module.exports = withNativeWind(config, {
  input: "./src/global.css",
  inlineVariables: false,
  globalClassNamePolyfill: false,
});
