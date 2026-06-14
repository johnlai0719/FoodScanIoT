import React, { useRef, useState } from 'react';
import {
  View, Text, Pressable, Modal, StyleSheet, Dimensions,
  Image, ScrollView, ActivityIndicator, Platform, Alert,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as ImageManipulator from 'expo-image-manipulator';
import {
  X, Camera, ScanLine, Zap, ArrowLeft, ImagePlus, Trash2,
} from 'lucide-react-native';

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');
const BOX_W = 280;
const BOX_BARCODE_H = 180;
const BOX_PHOTO_H = 350;

interface Props {
  visible: boolean;
  onClose: () => void;
  onBarcodeScanned: (code: string) => void;
  onPhotosSubmit: (photos: string[]) => void;
}

export default function BarcodeScanner({ visible, onClose, onBarcodeScanned, onPhotosSubmit }: Props) {
  const [mode, setMode] = useState<'barcode' | 'photo'>('barcode');
  const [scanned, setScanned] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [photos, setPhotos] = useState<string[]>([]);
  const cameraRef = useRef<CameraView>(null);
  const isProcessing = useRef(false);

  const [permission, requestPermission] = useCameraPermissions();

  const reset = () => {
    setScanned(false);
    setPhotos([]);
    setMode('barcode');
    isProcessing.current = false;
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleBarcodeScanned = ({ data }: { data: string }) => {
    if (isProcessing.current || scanned || mode === 'photo') return;
    isProcessing.current = true;
    setScanned(true);
    reset();
    onBarcodeScanned(data);
    onClose();
  };

  const handleCapture = async () => {
    if (!cameraRef.current || capturing) return;
    setCapturing(true);
    try {
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.8 });
      if (!photo?.uri) return;

      const boxH = BOX_PHOTO_H;
      const cropX = (SCREEN_W - BOX_W) / 2 / SCREEN_W;
      const cropY = (SCREEN_H - boxH) / 2 / SCREEN_H;
      const cropW = BOX_W / SCREEN_W;
      const cropH = boxH / SCREEN_H;

      const iW = photo.width;
      const iH = photo.height;
      const ox = Math.max(0, Math.floor(iW * cropX));
      const oy = Math.max(0, Math.floor(iH * cropY));
      const fw = Math.min(iW - ox, Math.floor(iW * cropW));
      const fh = Math.min(iH - oy, Math.floor(iH * cropH));

      const cropped = await ImageManipulator.manipulateAsync(
        photo.uri,
        [{ crop: { originX: ox, originY: oy, width: fw, height: fh } }],
        { compress: 0.7, format: ImageManipulator.SaveFormat.JPEG, base64: true },
      );
      if (cropped.base64) {
        setPhotos(prev => [...prev, cropped.base64!]);
      }
    } catch (e: any) {
      Alert.alert('拍照失敗', e.message);
    } finally {
      setCapturing(false);
    }
  };

  const handleSubmit = () => {
    if (photos.length === 0) return;
    const batch = [...photos];
    reset();
    onPhotosSubmit(batch);
    onClose();
  };

  const switchToPhoto = () => {
    setScanned(false);
    isProcessing.current = false;
    setMode('photo');
  };

  if (!visible) return null;

  if (!permission) {
    return (
      <Modal visible={visible} animationType="slide">
        <View style={s.center}>
          <ActivityIndicator size="large" color="#009B52" />
        </View>
      </Modal>
    );
  }

  if (!permission.granted) {
    return (
      <Modal visible={visible} animationType="slide">
        <View style={s.center}>
          <Camera size={48} color="#009B52" />
          <Text style={s.permTitle}>需要相機權限</Text>
          <Text style={s.permSub}>請允許 FoodScan 使用相機以掃描條碼</Text>
          <Pressable style={s.permBtn} onPress={requestPermission}>
            <Text style={s.permBtnText}>授權相機</Text>
          </Pressable>
          <Pressable style={s.permClose} onPress={handleClose}>
            <Text style={s.permCloseText}>取消</Text>
          </Pressable>
        </View>
      </Modal>
    );
  }

  const boxH = mode === 'barcode' ? BOX_BARCODE_H : BOX_PHOTO_H;
  const boxTop = (SCREEN_H - boxH) / 2;
  const boxLeft = (SCREEN_W - BOX_W) / 2;

  return (
    <Modal visible={visible} animationType="slide" statusBarTranslucent>
      <View style={{ flex: 1, backgroundColor: '#000' }}>
        <CameraView
          ref={cameraRef}
          style={StyleSheet.absoluteFill}
          onBarcodeScanned={scanned || mode === 'photo' ? undefined : handleBarcodeScanned}
        />

        {/* Dark overlay with hole */}
        <View style={StyleSheet.absoluteFill} pointerEvents="none">
          {/* Top */}
          <View style={{ height: boxTop, backgroundColor: 'rgba(0,0,0,0.55)' }} />
          {/* Middle row */}
          <View style={{ height: boxH, flexDirection: 'row' }}>
            <View style={{ width: boxLeft, backgroundColor: 'rgba(0,0,0,0.55)' }} />
            <View style={{ width: BOX_W }} />
            <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.55)' }} />
          </View>
          {/* Bottom */}
          <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.55)' }} />
        </View>

        {/* Corner brackets */}
        <View style={[s.bracket, { top: boxTop, left: boxLeft }]}>
          <View style={[s.cornerTL, mode === 'barcode' ? s.cornerGreen : s.cornerAmber]} />
        </View>
        <View style={[s.bracket, { top: boxTop, left: boxLeft + BOX_W - 28 }]}>
          <View style={[s.cornerTR, mode === 'barcode' ? s.cornerGreen : s.cornerAmber]} />
        </View>
        <View style={[s.bracket, { top: boxTop + boxH - 28, left: boxLeft }]}>
          <View style={[s.cornerBL, mode === 'barcode' ? s.cornerGreen : s.cornerAmber]} />
        </View>
        <View style={[s.bracket, { top: boxTop + boxH - 28, left: boxLeft + BOX_W - 28 }]}>
          <View style={[s.cornerBR, mode === 'barcode' ? s.cornerGreen : s.cornerAmber]} />
        </View>

        {/* Scan line (barcode mode) */}
        {mode === 'barcode' && (
          <View style={[s.scanLine, { top: boxTop + boxH / 2, left: boxLeft, width: BOX_W }]} />
        )}

        {/* Center label */}
        <View style={[s.labelWrap, { top: boxTop + boxH + 20 }]}>
          {mode === 'barcode' ? (
            <View style={s.labelPill}>
              <ScanLine size={13} color="#fff" />
              <Text style={s.labelText}>對準食品條碼自動掃描</Text>
            </View>
          ) : (
            <View style={[s.labelPill, { backgroundColor: 'rgba(245,158,11,0.85)' }]}>
              <Camera size={13} color="#fff" />
              <Text style={s.labelText}>對準成分表區域拍照</Text>
            </View>
          )}
        </View>

        {/* Top controls */}
        <View style={s.topBar}>
          <Pressable style={s.iconBtn} onPress={mode === 'photo' ? () => setMode('barcode') : handleClose}>
            <ArrowLeft size={22} color="#fff" />
          </Pressable>
          <View style={s.modeToggle}>
            <Pressable
              style={[s.modeBtn, mode === 'barcode' && s.modeBtnActive]}
              onPress={() => setMode('barcode')}
            >
              <ScanLine size={13} color={mode === 'barcode' ? '#fff' : 'rgba(255,255,255,0.5)'} />
              <Text style={[s.modeBtnText, mode === 'barcode' && { color: '#fff' }]}>條碼</Text>
            </Pressable>
            <Pressable
              style={[s.modeBtn, mode === 'photo' && s.modeBtnActiveAmber]}
              onPress={switchToPhoto}
            >
              <Camera size={13} color={mode === 'photo' ? '#fff' : 'rgba(255,255,255,0.5)'} />
              <Text style={[s.modeBtnText, mode === 'photo' && { color: '#fff' }]}>拍照</Text>
            </Pressable>
          </View>
          <Pressable style={s.iconBtn} onPress={handleClose}>
            <X size={22} color="#fff" />
          </Pressable>
        </View>

        {/* Photo thumbnails */}
        {mode === 'photo' && photos.length > 0 && (
          <View style={[s.thumbnailBar, { bottom: 180 }]}>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 20, gap: 10 }}>
              {photos.map((p, i) => (
                <View key={i} style={s.thumbWrap}>
                  <Image source={{ uri: `data:image/jpeg;base64,${p}` }} style={s.thumb} />
                  <Pressable style={s.thumbDel} onPress={() => setPhotos(prev => prev.filter((_, idx) => idx !== i))}>
                    <Trash2 size={10} color="#fff" />
                  </Pressable>
                </View>
              ))}
            </ScrollView>
          </View>
        )}

        {/* Bottom controls (photo mode) */}
        {mode === 'photo' && (
          <View style={s.bottomBar}>
            <Pressable style={s.captureBtn} onPress={handleCapture} disabled={capturing}>
              {capturing
                ? <ActivityIndicator color="#009B52" />
                : <Camera size={28} color="#009B52" />}
            </Pressable>
            {photos.length > 0 && (
              <Pressable style={s.submitBtn} onPress={handleSubmit}>
                <Zap size={16} color="#fff" />
                <Text style={s.submitBtnText}>送出分析 ({photos.length})</Text>
              </Pressable>
            )}
          </View>
        )}
      </View>
    </Modal>
  );
}

const CORNER_SIZE = 28;
const CORNER_THICKNESS = 3;

const s = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 16, backgroundColor: '#FDF8F3', padding: 32 },
  permTitle: { fontSize: 18, fontWeight: '800', color: '#1A1A1A', marginTop: 8 },
  permSub: { fontSize: 13, color: '#757575', textAlign: 'center', lineHeight: 20 },
  permBtn: { backgroundColor: '#009B52', paddingVertical: 14, paddingHorizontal: 32, borderRadius: 14, marginTop: 8 },
  permBtnText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  permClose: { paddingVertical: 10 },
  permCloseText: { color: '#757575', fontWeight: '600' },

  bracket: { position: 'absolute', width: CORNER_SIZE, height: CORNER_SIZE },
  cornerTL: { position: 'absolute', top: 0, left: 0, width: CORNER_SIZE, height: CORNER_SIZE, borderTopWidth: CORNER_THICKNESS, borderLeftWidth: CORNER_THICKNESS, borderRadius: 4 },
  cornerTR: { position: 'absolute', top: 0, right: 0, width: CORNER_SIZE, height: CORNER_SIZE, borderTopWidth: CORNER_THICKNESS, borderRightWidth: CORNER_THICKNESS, borderRadius: 4 },
  cornerBL: { position: 'absolute', bottom: 0, left: 0, width: CORNER_SIZE, height: CORNER_SIZE, borderBottomWidth: CORNER_THICKNESS, borderLeftWidth: CORNER_THICKNESS, borderRadius: 4 },
  cornerBR: { position: 'absolute', bottom: 0, right: 0, width: CORNER_SIZE, height: CORNER_SIZE, borderBottomWidth: CORNER_THICKNESS, borderRightWidth: CORNER_THICKNESS, borderRadius: 4 },
  cornerGreen: { borderColor: '#2D6A4F' },
  cornerAmber: { borderColor: '#f59e0b' },

  scanLine: {
    position: 'absolute', height: 2,
    backgroundColor: 'rgba(45,106,79,0.7)',
  },

  labelWrap: { position: 'absolute', left: 0, right: 0, alignItems: 'center' },
  labelPill: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: 'rgba(45,106,79,0.85)',
    paddingHorizontal: 16, paddingVertical: 8, borderRadius: 20,
  },
  labelText: { color: '#fff', fontWeight: '700', fontSize: 13 },

  topBar: {
    position: 'absolute', top: Platform.OS === 'ios' ? 56 : 40,
    left: 16, right: 16,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
  },
  iconBtn: {
    backgroundColor: 'rgba(0,0,0,0.45)', borderRadius: 20,
    padding: 10, borderWidth: 1, borderColor: 'rgba(255,255,255,0.15)',
  },
  modeToggle: {
    flexDirection: 'row', backgroundColor: 'rgba(0,0,0,0.5)',
    borderRadius: 20, padding: 3, gap: 2,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.15)',
  },
  modeBtn: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 14, paddingVertical: 7, borderRadius: 17 },
  modeBtnActive: { backgroundColor: '#2D6A4F' },
  modeBtnActiveAmber: { backgroundColor: '#b45309' },
  modeBtnText: { fontSize: 12, fontWeight: '700', color: 'rgba(255,255,255,0.5)' },

  thumbnailBar: { position: 'absolute', left: 0, right: 0, height: 90 },
  thumbWrap: { width: 78, height: 78, borderRadius: 14, overflow: 'hidden', borderWidth: 2, borderColor: '#f59e0b' },
  thumb: { width: '100%', height: '100%' },
  thumbDel: {
    position: 'absolute', top: 4, right: 4,
    backgroundColor: 'rgba(220,38,38,0.85)', borderRadius: 8,
    width: 20, height: 20, alignItems: 'center', justifyContent: 'center',
  },

  bottomBar: {
    position: 'absolute', bottom: 56, left: 0, right: 0,
    alignItems: 'center', gap: 20,
  },
  captureBtn: {
    width: 76, height: 76, borderRadius: 38,
    backgroundColor: '#fff',
    alignItems: 'center', justifyContent: 'center',
    borderWidth: 4, borderColor: 'rgba(255,255,255,0.4)',
    shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 10, shadowOffset: { width: 0, height: 4 },
    elevation: 8,
  },
  submitBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: '#009B52', paddingHorizontal: 28, paddingVertical: 14,
    borderRadius: 24,
    shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 8, shadowOffset: { width: 0, height: 3 },
    elevation: 5,
  },
  submitBtnText: { color: '#fff', fontWeight: '800', fontSize: 15 },
});
