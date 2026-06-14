import './src/global.css';
import { useEffect, useRef, useState } from 'react';
import { Animated, StyleSheet, Text, View } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { SvgUri } from 'react-native-svg';
import { Asset } from 'expo-asset';
import HomeScreen from './src/screens/HomeScreen';
import { FontScaleProvider } from './src/contexts/FontScaleContext';

function SplashOverlay({ opacity }: { opacity: Animated.Value }) {
  const [iconUri, setIconUri] = useState<string | null>(null);

  useEffect(() => {
    Asset.fromModule(require('./assets/images/FoodScan_Icon.svg'))
      .downloadAsync()
      .then(a => setIconUri(a.localUri ?? a.uri))
      .catch(() => {});
  }, []);

  return (
    <Animated.View style={[styles.splash, { opacity }]}>
      {iconUri ? (
        <SvgUri uri={iconUri} width={160} height={160} />
      ) : (
        <View style={styles.iconPlaceholder} />
      )}
      <Text style={styles.title}>FoodScan</Text>
      <Text style={styles.subtitle}>智慧食品成分辨識</Text>
    </Animated.View>
  );
}

export default function App() {
  const [showSplash, setShowSplash] = useState(true);
  const opacity = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    const timer = setTimeout(() => {
      Animated.timing(opacity, {
        toValue: 0,
        duration: 600,
        useNativeDriver: true,
      }).start(() => setShowSplash(false));
    }, 1800);
    return () => clearTimeout(timer);
  }, []);

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <FontScaleProvider>
          <HomeScreen />
          {showSplash && <SplashOverlay opacity={opacity} />}
        </FontScaleProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  splash: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#F5F5F5',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 16,
    zIndex: 999,
  },
  iconPlaceholder: {
    width: 160,
    height: 160,
  },
  title: {
    fontSize: 28,
    fontWeight: '800',
    color: '#009B52',
    letterSpacing: 1,
  },
  subtitle: {
    fontSize: 14,
    color: '#757575',
    fontWeight: '500',
    letterSpacing: 0.5,
  },
});
