const { getDefaultConfig } = require("expo/metro-config");
const { withNativewind } = require("nativewind/metro");

/** @type {import('expo/metro-config').MetroConfig} */
const config = getDefaultConfig(__dirname);

// 【最佳化】針對 Cloudflare Tunnel 允許自定義 Host 連線
// 確保 Metro 伺服器不會因為網域不符 (Invalid Host Header) 而阻擋請求
config.server = {
  ...config.server,
  rewriteRequestUrl: (url) => {
    return url;
  },
};

module.exports = withNativewind(config, {
  input: "./src/global.css",
  inlineVariables: false,
  globalClassNamePolyfill: false,
});
