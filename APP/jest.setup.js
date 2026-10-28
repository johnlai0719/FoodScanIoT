// AsyncStorage 是原生模組，在 jest 裡載入會拋 "NativeModule is null"。
// 套件自己附了官方 mock，掛在這裡讓所有測試共用——否則每個碰到
// AsyncStorage 的測試都要自己 jest.mock 一次，漏掉的那個就會紅。
jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'));
