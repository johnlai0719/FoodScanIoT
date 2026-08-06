import React, { useState } from 'react';
import { View, Text, Image, Alert, Pressable, StyleSheet, ScrollView } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import * as ImageManipulator from 'expo-image-manipulator';
import { Camera, ImagePlus, Trash2, CheckCircle2 } from 'lucide-react-native';

// 上傳前壓縮的參數。與 測試/量化測試/compress_images.py 的預設值相同——
// 那支腳本用同一組參數處理測試集後重跑辨識，量的就是這裡送出去的東西。
// 改動這兩個數字前先跑一次該實驗，否則辨識率的變化沒有依據。
const MAX_WIDTH = 1280;
const QUALITY = 0.8;

interface Props {
  onImageCaptured: (base64: string) => void;
  images?: string[];
  onRemoveImage?: (index: number) => void;
}

export default function PackageImageScanner({ onImageCaptured, images = [], onRemoveImage }: Props) {
  const [loading, setLoading] = useState<'camera' | 'library' | null>(null);

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
      // 只縮不放。resize 設的是**結果尺寸**而非上限，寬度本來就小於 1280 的圖
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
                <Image
                  source={{ uri: img.startsWith('data:') ? img : `data:image/jpeg;base64,${img}` }}
                  style={s.previewImg}
                />
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
  previewImg: { width: '100%', height: '100%' },
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
