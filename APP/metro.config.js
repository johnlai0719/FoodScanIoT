const { getDefaultConfig } = require("expo/metro-config");
const { withNativeWind } = require("nativewind/metro");
const { existsSync } = require("node:fs");
const path = require("node:path");

const config = getDefaultConfig(__dirname);
config.server = { ...config.server, rewriteRequestUrl: (url) => url };
const catAsset = path.join(__dirname, "assets", "images", "elthen-cat.png");
config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (moduleName === "foodscan-elthen-cat") {
    return existsSync(catAsset)
      ? context.resolveRequest(context, catAsset, platform)
      : { type: "empty" };
  }
  return context.resolveRequest(context, moduleName, platform);
};

module.exports = withNativeWind(config, {
  input: "./src/global.css",
  inlineVariables: false,
  globalClassNamePolyfill: false,
});
