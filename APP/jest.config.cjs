// SDK 58 may keep expo-modules-core nested under expo. Resolve its public entry for the Jest preset.
module.exports = {
  preset: 'jest-expo',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  transformIgnorePatterns: [
    'node_modules/(?!(?:.pnpm/)?((jest-)?react-native|@react-native(-community)?|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|@sentry/react-native|native-base|react-native-svg|nativewind|react-native-css|lucide-react-native))',
  ],
  moduleNameMapper: {
    '^expo-modules-core$': require.resolve('expo-modules-core', { paths: [require.resolve('expo/package.json')] }),
  },
};
