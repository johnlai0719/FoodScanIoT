import React, { useState } from 'react';
import { View, Text, Image, Alert, Pressable, StyleSheet, ScrollView } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { Camera, ImagePlus, Trash2, CheckCircle2 } from 'lucide-react-native';

interface Props {
  onImageCaptured: (base64: string) => void;
  images?: string[];
  onRemoveImage?: (index: number) => void;
}

export default function PackageImageScanner({ onImageCaptured, images = [], onRemoveImage }: Props) {
  const [loading, setLoading] = useState<'camera' | 'library' | null>(null);

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
        quality: 0.8,
        base64: true,
      });
      if (!result.canceled && result.assets[0].base64) {
        onImageCaptured(result.assets[0].base64);
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
        quality: 0.8,
        base64: true,
        allowsMultipleSelection: false,
      });
      if (!result.canceled && result.assets[0].base64) {
        onImageCaptured(result.assets[0].base64);
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
