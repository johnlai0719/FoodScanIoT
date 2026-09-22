import React, { useMemo, useState } from 'react';
import { View, Text, Image, Alert, Pressable, StyleSheet, ScrollView, Modal } from 'react-native';
import { Gesture, GestureDetector, GestureHandlerRootView } from 'react-native-gesture-handler';
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';
import * as ImagePicker from 'expo-image-picker';
import * as ImageManipulator from 'expo-image-manipulator';
import { Camera, ImagePlus, Trash2, CheckCircle2, X } from 'lucide-react-native';

// 上傳前壓縮的參數。
// 依據實驗 05（05-壓縮對本地管線之影響），若壓至 1280px 會使本地 vlcrop 管線添加物 F1 重挫 9.7 點；
// 提升至最長邊 1920px (JPEG quality 0.85) 則可通過統計非劣性驗證（差距僅 -1.6 點，CI 跨 0），
// 橫向拍攝即對應 1920x1080 (FHD)，直向拍攝寬度保留 1920px 以維護橫排小字辨識率。
const MAX_WIDTH = 1920;
const QUALITY = 0.85;

// 放大檢視器的縮放範圍。上限比整頁縮放（4×）高：這裡要看的是標示上的小字，
// 而照片本身最長邊有 1920px，放到 6 倍仍在原始解析度內，不會只是放大馬賽克。
const MIN_ZOOM = 1;
const MAX_ZOOM = 6;

const clampZoom = (value: number) => {
  'worklet';
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
};

/** 夾住位移，讓放大的圖不會被拖出畫面外變成一片黑。 */
const clampPan = (value: number, scale: number, size: number) => {
  'worklet';
  const max = (size * (scale - 1)) / 2;
  return Math.min(max, Math.max(-max, value));
};

const toUri = (img: string) =>
  img.startsWith('data:') ? img : `data:image/jpeg;base64,${img}`;

interface Props {
  onImageCaptured: (base64: string) => void;
  images?: string[];
  onRemoveImage?: (index: number) => void;
}

/**
 * 全螢幕檢視器：雙指縮放、拖曳平移。
 *
 * 為什麼要獨立成元件：Modal 的內容渲染在**另一個原生視窗**，不在
 * HomeScreen 那層 GestureHandlerRootView 底下，手勢收不到。所以這裡要自己
 * 再包一層 GestureHandlerRootView，而且縮放狀態也必須是自己的
 * （不能沿用整頁縮放的那組 shared value）。
 *
 * key 用 index 讓每次開啟都是新的實例，縮放狀態自然歸零——否則關掉再開
 * 會停在上次放大的位置。
 */
function ImageViewer({ uri, index, total, onClose }: {
  uri: string; index: number; total: number; onClose: () => void;
}) {
  const scale = useSharedValue(1);
  const tx = useSharedValue(0);
  const ty = useSharedValue(0);
  const viewW = useSharedValue(0);
  const viewH = useSharedValue(0);
  // 增量更新：縮放與拖曳同時進行時才不會互相覆蓋對方寫進去的位移。
  const prevScale = useSharedValue(1);
  const prevX = useSharedValue(0);
  const prevY = useSharedValue(0);

  const gesture = useMemo(() => {
    const pinch = Gesture.Pinch()
      .onStart(() => { prevScale.value = 1; })
      .onUpdate(e => {
        const factor = e.scale / prevScale.value;
        prevScale.value = e.scale;
        const next = clampZoom(scale.value * factor);
        // 夾到上下限後真正生效的倍率，可能小於手指給的
        const applied = next / scale.value;
        const fx = e.focalX - viewW.value / 2;
        const fy = e.focalY - viewH.value / 2;
        tx.value = clampPan(fx - (fx - tx.value) * applied, next, viewW.value);
        ty.value = clampPan(fy - (fy - ty.value) * applied, next, viewH.value);
        scale.value = next;
      })
      .onEnd(() => {
        if (scale.value <= MIN_ZOOM + 0.01) {
          scale.value = withTiming(MIN_ZOOM, { duration: 160 });
          tx.value = withTiming(0, { duration: 160 });
          ty.value = withTiming(0, { duration: 160 });
        }
      });

    // 單指就能拖：這裡沒有 ScrollView 要讓，不必像結果頁那樣限定兩指。
    const pan = Gesture.Pan()
      .onStart(() => { prevX.value = 0; prevY.value = 0; })
      .onUpdate(e => {
        const dx = e.translationX - prevX.value;
        const dy = e.translationY - prevY.value;
        prevX.value = e.translationX;
        prevY.value = e.translationY;
        tx.value = clampPan(tx.value + dx, scale.value, viewW.value);
        ty.value = clampPan(ty.value + dy, scale.value, viewH.value);
      });

    // 雙擊在 1× 與 2× 之間切換。放大後要看別處時，雙擊歸位比掐回去快。
    const doubleTap = Gesture.Tap()
      .numberOfTaps(2)
      .onEnd(() => {
        const to = scale.value > MIN_ZOOM + 0.01 ? MIN_ZOOM : 2;
        scale.value = withTiming(to, { duration: 180 });
        tx.value = withTiming(0, { duration: 180 });
        ty.value = withTiming(0, { duration: 180 });
      });

    return Gesture.Simultaneous(Gesture.Exclusive(doubleTap, pan), pinch);
  }, []);

  const style = useAnimatedStyle(() => ({
    transform: [
      { translateX: tx.value },
      { translateY: ty.value },
      { scale: scale.value },
    ],
  }));

  return (
    <Modal visible transparent={false} animationType="fade"
           statusBarTranslucent onRequestClose={onClose}>
      {/* Modal 在另一個原生視窗，手勢要自己再包一層 root */}
      <GestureHandlerRootView style={s.viewerRoot}>
        <View
          style={s.viewerStage}
          onLayout={e => {
            viewW.value = e.nativeEvent.layout.width;
            viewH.value = e.nativeEvent.layout.height;
          }}
        >
          <GestureDetector gesture={gesture}>
            <Animated.View style={[s.viewerStage, style]}>
              {/* contain：不裁切。這張是要拿來看清楚標示的，不是拿來排版好看的 */}
              <Image source={{ uri }} style={s.viewerImg} resizeMode="contain" />
            </Animated.View>
          </GestureDetector>
        </View>

        <Pressable style={s.viewerClose} onPress={onClose}
                   accessibilityRole="button" accessibilityLabel="關閉">
          <X size={20} color="#fff" />
        </Pressable>
        <View style={s.viewerHint}>
          <Text style={s.viewerHintText}>
            第 {index + 1} / {total} 張・雙指縮放、拖曳移動、雙擊放大
          </Text>
        </View>
      </GestureHandlerRootView>
    </Modal>
  );
}

export default function PackageImageScanner({ onImageCaptured, images = [], onRemoveImage }: Props) {
  const [loading, setLoading] = useState<'camera' | 'library' | null>(null);
  // 正在全螢幕檢視第幾張。null 代表沒開。
  const [viewing, setViewing] = useState<number | null>(null);

  /**
   * 縮圖、壓縮、轉 base64。
   *
   * 不在 ImagePicker 的選項裡直接要 `quality` 與 `base64`：那樣拿到的是
   * **原始解析度**的 base64（手機主鏡頭動輒 4000px 寬），既白編碼一次，
   * 上傳量也是這裡的數倍。改由 ImageManipulator 一次完成縮圖與壓縮。
   *
   * 順帶解掉 iOS 相簿的 HEIC——原本會把 HEIC 的 base64 直接往上送。
   */
  const processAndCompressImage = async (uri: string, srcWidth: number) => {
    try {
      // 只縮不放。resize 設的是**結果尺寸**而非上限，寬度本來就小於 1920 的圖
      // （截圖、網路存下的小圖）若照樣傳進去會被放大：檔案變大，細節一點沒多。
      const actions = srcWidth > MAX_WIDTH ? [{ resize: { width: MAX_WIDTH } }] : [];
      const out = await ImageManipulator.manipulateAsync(uri, actions, {
        compress: QUALITY,
        format: ImageManipulator.SaveFormat.JPEG,
        base64: true,
      });
      if (out.base64) {
        // 實機驗收用：確認送出去的確實是這組參數壓過的結果。
        // 離線實驗（compress_images.py）只證明「這個等級夠辨識」，
        // 證明「App 真的有壓」要看這行。
        console.log(`[compress] ${srcWidth}px → ${out.width}px, `
          + `base64 ${(out.base64.length / 1024).toFixed(0)}KB`);
        onImageCaptured(out.base64);
      }
    } catch (e: any) {
      console.error('影像壓縮失敗', e);
      Alert.alert('影像處理失敗', '請再試一次，或改用其他照片');
    }
  };

  const takePhoto = async () => {
    const { granted } = await ImagePicker.requestCameraPermissionsAsync();
    if (!granted) {
      Alert.alert('權限拒絕', '請在設定中允許 FoodScan 使用相機');
      return;
    }
    setLoading('camera');
    try {
      const result = await ImagePicker.launchCameraAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        allowsEditing: false,
        // quality／base64 交給 ImageManipulator，見 processAndCompressImage
      });
      if (!result.canceled && result.assets[0].uri) {
        await processAndCompressImage(result.assets[0].uri, result.assets[0].width);
      }
    } finally {
      setLoading(null);
    }
  };

  const pickFromLibrary = async () => {
    const { granted } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!granted) {
      Alert.alert('權限拒絕', '請在設定中允許 FoodScan 存取相簿');
      return;
    }
    setLoading('library');
    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        allowsEditing: false,
        allowsMultipleSelection: false,
        // quality／base64 交給 ImageManipulator，見 processAndCompressImage
      });
      if (!result.canceled && result.assets[0].uri) {
        await processAndCompressImage(result.assets[0].uri, result.assets[0].width);
      }
    } finally {
      setLoading(null);
    }
  };

  return (
    <View style={s.container}>
      {/* Action buttons */}
      <View style={s.btnRow}>
        <Pressable
          style={[s.actionBtn, s.actionBtnPrimary, loading === 'camera' && s.actionBtnLoading]}
          onPress={takePhoto}
          disabled={!!loading}
        >
          <View style={s.actionBtnIcon}>
            <Camera size={20} color="#009B52" />
          </View>
          <View>
            <Text style={s.actionBtnLabel}>相機拍照</Text>
            <Text style={s.actionBtnSub}>拍攝成分標籤</Text>
          </View>
        </Pressable>

        <Pressable
          style={[s.actionBtn, s.actionBtnSecondary, loading === 'library' && s.actionBtnLoading]}
          onPress={pickFromLibrary}
          disabled={!!loading}
        >
          <View style={[s.actionBtnIcon, s.actionBtnIconSecondary]}>
            <ImagePlus size={20} color="#757575" />
          </View>
          <View>
            <Text style={[s.actionBtnLabel, s.actionBtnLabelSecondary]}>選取相簿</Text>
            <Text style={s.actionBtnSub}>從裝置選圖</Text>
          </View>
        </Pressable>
      </View>

      {/* Image previews */}
      {images.length > 0 && (
        <View style={s.previewSection}>
          <View style={s.previewHeader}>
            <CheckCircle2 size={13} color="#009B52" />
            <Text style={s.previewCount}>{images.length} 張已選取</Text>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.previewScroll}>
            {images.map((img, i) => (
              <View key={i} style={s.previewItem}>
                {/* 點縮圖看大圖。縮圖只有 72px，標示上的小字在這個尺寸下
                    根本看不出拍清楚了沒有——而那正是使用者按下「開始分析」
                    之前唯一想確認的事。 */}
                <Pressable
                  onPress={() => setViewing(i)}
                  style={s.previewImgWrap}
                  accessibilityRole="imagebutton"
                  accessibilityLabel={`放大檢視第 ${i + 1} 張`}
                >
                  <Image source={{ uri: toUri(img) }} style={s.previewImg} />
                </Pressable>
                {onRemoveImage && (
                  <Pressable style={s.removeBtn} onPress={() => onRemoveImage(i)}>
                    <Trash2 size={10} color="#fff" />
                  </Pressable>
                )}
                <View style={s.previewNum}>
                  <Text style={s.previewNumText}>{i + 1}</Text>
                </View>
              </View>
            ))}
          </ScrollView>
        </View>
      )}

      {/* key 用 index：每次開啟都是新實例，縮放狀態自然歸零 */}
      {viewing !== null && images[viewing] && (
        <ImageViewer
          key={viewing}
          uri={toUri(images[viewing])}
          index={viewing}
          total={images.length}
          onClose={() => setViewing(null)}
        />
      )}

      {images.length === 0 && (
        <View style={s.emptyHint}>
          <Text style={s.emptyHintText}>上傳食品包裝標籤或成分表照片以啟動 AI 解析</Text>
        </View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  container: { gap: 12 },

  btnRow: { flexDirection: 'row', gap: 10 },

  actionBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', gap: 10,
    padding: 14, borderRadius: 16, borderWidth: 1,
  },
  actionBtnPrimary: { backgroundColor: '#E6F7EE', borderColor: '#DDDDDD' },
  actionBtnSecondary: { backgroundColor: '#fff', borderColor: '#DDDDDD' },
  actionBtnLoading: { opacity: 0.5 },

  actionBtnIcon: {
    width: 38, height: 38, borderRadius: 12,
    backgroundColor: '#fff', borderWidth: 1, borderColor: '#DDDDDD',
    alignItems: 'center', justifyContent: 'center',
  },
  actionBtnIconSecondary: { backgroundColor: '#E6F7EE' },

  actionBtnLabel: { fontSize: 13, fontWeight: '700', color: '#1A1A1A' },
  actionBtnLabelSecondary: { color: '#009B52' },
  actionBtnSub: { fontSize: 10, color: '#757575', marginTop: 1 },

  previewSection: { gap: 8 },
  previewHeader: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  previewCount: { fontSize: 11, fontWeight: '700', color: '#009B52' },
  previewScroll: { gap: 10, paddingVertical: 2 },

  previewItem: { width: 84, height: 84, borderRadius: 14, overflow: 'hidden', position: 'relative' },
  previewImgWrap: { width: '100%', height: '100%' },
  previewImg: { width: '100%', height: '100%' },

  // 全螢幕檢視器
  viewerRoot: { flex: 1, backgroundColor: '#000' },
  viewerStage: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  viewerImg: { width: '100%', height: '100%' },
  viewerClose: {
    position: 'absolute', top: 44, right: 16,
    width: 36, height: 36, borderRadius: 18,
    alignItems: 'center', justifyContent: 'center',
    backgroundColor: 'rgba(0,0,0,0.55)',
  },
  viewerHint: {
    position: 'absolute', bottom: 32, alignSelf: 'center',
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 14,
    backgroundColor: 'rgba(0,0,0,0.55)',
  },
  viewerHintText: { fontSize: 11, color: '#fff', fontWeight: '600' },
  removeBtn: {
    position: 'absolute', top: 5, right: 5,
    backgroundColor: 'rgba(220,38,38,0.85)', borderRadius: 8,
    width: 20, height: 20, alignItems: 'center', justifyContent: 'center',
  },
  previewNum: {
    position: 'absolute', bottom: 5, left: 5,
    backgroundColor: 'rgba(0,0,0,0.55)', borderRadius: 6,
    paddingHorizontal: 5, paddingVertical: 2,
  },
  previewNumText: { color: '#fff', fontSize: 9, fontWeight: '800' },

  emptyHint: {
    backgroundColor: '#E6F7EE', borderRadius: 12, padding: 12,
    borderWidth: 1, borderColor: '#DDDDDD', borderStyle: 'dashed',
    alignItems: 'center',
  },
  emptyHintText: { fontSize: 11, color: '#757575', textAlign: 'center', lineHeight: 16 },
});
