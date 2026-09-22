import './src/global.css';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import HomeScreen from './src/screens/HomeScreen';
import { FontScaleProvider } from './src/contexts/FontScaleContext';

/**
 * 2026-09-22 移除開場的 LOGO 轉場動畫（SplashOverlay）。
 *
 * 它是一層蓋在 HomeScreen 上的 Animated.View，固定停留 1.8 秒後再花 0.6 秒
 * 淡出——也就是**每次開 App 都要先等 2.4 秒**才碰得到畫面，而它不提供任何
 * 資訊（下面的 HomeScreen 其實早就準備好了，只是被蓋住）。展示與測試時
 * 尤其惱人：每重載一次就再等一次。
 *
 * 要找回來的話看這個檔案在 e0c1234 之前的版本，整段連同樣式都在那裡。
 * 若之後要重做，建議改成「資料真的還沒好才顯示」而不是固定秒數，
 * 否則就只是把等待時間寫死。
 */
export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <FontScaleProvider>
          <HomeScreen />
        </FontScaleProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
