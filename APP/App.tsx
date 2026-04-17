import "./src/global.css";
import { registerRootComponent } from 'expo';
import React, { useState, useEffect, useRef } from 'react';
import { 
  ActivityIndicator,
  Alert,
  StatusBar,
  Text as RNText,
  Dimensions,
  Image,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as ImageManipulator from 'expo-image-manipulator';
import * as Haptics from 'expo-haptics';
import * as SQLite from 'expo-sqlite';
import LottieView from 'lottie-react-native';

// 關鍵組件匯入 (經過包裝)
import { View, Text, ScrollView, Pressable, TextInput } from './src/tw';

// 業務邏輯與型別
import { fetchFromFog, clearFogCache } from './services/foodService';
import { ScanResult, UserProfile } from './types';
import * as StorageService from './services/storageService';

// UI 子組件
import { HealthReport } from './components/HealthReport';
import { IngredientCard } from './components/IngredientCard';

// 穩定版圖標 (使用 Lucide React Native 專業圖示)
import { 
  User as UserIcon, 
  Search as ScanIcon, 
  ArrowLeft as BackIcon, 
  Camera as CameraIcon, 
  Apple as AppleIcon, 
  FlaskConical as BeakerIcon, 
  Zap as ZapIcon, 
  AlertTriangle as WarningIcon, 
  ShieldCheck as ShieldIcon, 
  Info as InfoIcon,
  FlaskRound as FlaskIcon,
  Trash2 as TrashIcon
} from 'lucide-react-native';

/**
 * 分數儀表組件 (Mobile 版)
 */
function ScoreGauge({ score, grade }: { score: number, grade: string }) {
  const getColor = (g: string) => {
    if (g === 'A') return '#10b981';
    if (g === 'B') return '#3b82f6';
    if (g === 'C') return '#f59e0b';
    return '#ef4444';
  };

  return (
    <View style={{ alignItems: 'center', justifyContent: 'center', width: 140, height: 140 }}>
      <View style={{ 
        width: 120, height: 120, borderRadius: 60, 
        borderWidth: 10, borderColor: '#e2e8f0', 
        alignItems: 'center', justifyContent: 'center',
        position: 'relative'
      }}>
        <View style={{ 
          position: 'absolute', width: 120, height: 120, borderRadius: 60, 
          borderWidth: 10, borderColor: getColor(grade), borderTopColor: 'transparent',
          transform: [{ rotate: '45deg' }]
        }} />
        <Text style={{ fontSize: 42, fontWeight: '900', color: '#1e293b' }}>{grade}</Text>
        <Text style={{ fontSize: 13, fontWeight: '700', color: '#94a3b8' }}>{score} PTS</Text>
      </View>
    </View>
  );
}

const DEFAULT_PROFILE: UserProfile = {
  name: "開發者使用者",
  group: "adult",
  allergens: [],
  custom_allergens: [],
  healthConditions: []
};

const MARK_DEFINITIONS: Record<string, { title: string, desc: string, color: string, bgColor: string, textColor: string }> = {
  "CAS": { 
    title: "CAS 台灣優良農產品", 
    desc: "國產農產品及其加工品最高品質代表標章，確保安全、衛生、品質達標。", 
    color: "#ef4444", bgColor: "#fef2f2", textColor: "#991b1b"
  },
  "TQF": { 
    title: "TQF 台灣優良食品", 
    desc: "前身為 GMP，強調源頭管理、製程與成品檢驗，為食品安全雙重把關。", 
    color: "#3b82f6", bgColor: "#eff6ff", textColor: "#1e3a8a"
  },
  "TAP": { 
    title: "TAP 產銷履歷農產品", 
    desc: "產品從農場到餐桌流程透明、可追溯，且符合良好農業規範。", 
    color: "#059669", bgColor: "#f0fdf4", textColor: "#064e3b"
  },
  "健康食品": { 
    title: "健康食品 (小綠人)", 
    desc: "通過衛福部審查，具備特定保健功效，並確保安全與科學實證。", 
    color: "#16a34a", bgColor: "#f0fdf4", textColor: "#064e3b"
  },
  "有機農產品": { 
    title: "有機農產品標章", 
    desc: "不使用化學肥料、農藥，並經第三方驗證合格之環境友善產品。", 
    color: "#4d7c0f", bgColor: "#f7fee7", textColor: "#365314"
  }
};

function AppContent() {
  const [currentPage, setCurrentPage] = useState<'profile' | 'scanner' | 'result'>('profile');
  const [scannerMode, setScannerMode] = useState<'barcode' | 'photo'>('barcode');
  const [userProfile, setUserProfile] = useState<UserProfile>(DEFAULT_PROFILE);
  const [customConditionInput, setCustomConditionInput] = useState('');
  const [customAllergenInput, setCustomAllergenInput] = useState('');
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [scanned, setScanned] = useState(false);
  const [lastBarcode, setLastBarcode] = useState("");
  const [isTestMode, setIsTestMode] = useState(false);
  const [capturedPhotos, setCapturedPhotos] = useState<string[]>([]);
  const [scanHistory, setScanHistory] = useState<any[]>([]);
  
  const db = SQLite.useSQLiteContext();

  const refreshHistory = async () => {
    const history = await StorageService.getScanHistory(db);
    setScanHistory(history);
  };

  // 🛠️ 優化：SQLite 初始化與 Profile 讀取
  useEffect(() => {
    const initAndLoad = async () => {
      try {
        await StorageService.initDatabase(db);
        const savedProfile = await StorageService.loadUserProfile(db);
        if (savedProfile) {
          setUserProfile(savedProfile);
        }
        await refreshHistory();
      } catch (e) {
        console.error("[App] Storage Init Error", e);
      }
    };
    initAndLoad();
  }, [db]);

  // 🛠️ 優化：當 Profile 變動時自動同步到 SQLite
  useEffect(() => {
    StorageService.saveUserProfile(db, userProfile);
  }, [userProfile, db]);

  const triggerFeedback = (type: 'success' | 'warning' | 'light' = 'light') => {
    if (type === 'success') Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    else if (type === 'warning') Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    else Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  };
  const [expandChemical, setExpandChemical] = useState(true);

  const cameraRef = useRef<CameraView>(null);
  const isProcessing = useRef(false);
  const [permission, requestPermission] = useCameraPermissions();

  const resetScanner = () => {
    isProcessing.current = false;
    setScanned(false);
    setCapturedPhotos([]);
  };

  const addPhoto = (base64: string) => {
    setCapturedPhotos(prev => [...prev, base64]);
  };

  const removePhoto = (index: number) => {
    setCapturedPhotos(prev => prev.filter((_, i) => i !== index));
  };

  const handleCapturePhoto = async () => {
    if (!cameraRef.current || isLoading) return;
    try {
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.8 });
      if (photo?.uri) {
        const { width: screenWidth, height: screenHeight } = Dimensions.get('window');
        const boxW = 280;
        const boxH = 350;
        const cropX = (screenWidth - boxW) / 2 / screenWidth;
        const cropY = (screenHeight - boxH) / 2 / screenHeight;
        const cropW = boxW / screenWidth;
        const cropH = boxH / screenHeight;

        const imgW = photo.width;
        const imgH = photo.height;
        const finalOriginX = Math.max(0, Math.floor(imgW * cropX));
        const finalOriginY = Math.max(0, Math.floor(imgH * cropY));
        const finalWidth = Math.min(imgW - finalOriginX, Math.floor(imgW * cropW));
        const finalHeight = Math.min(imgH - finalOriginY, Math.floor(imgH * cropH));

        const cropped = await ImageManipulator.manipulateAsync(
          photo.uri,
          [{ crop: { originX: finalOriginX, originY: finalOriginY, width: finalWidth, height: finalHeight } }],
          { compress: 0.7, format: ImageManipulator.SaveFormat.JPEG, base64: true }
        );

        if (cropped.base64) {
          triggerFeedback('light');
          addPhoto(cropped.base64);
        }
      }
    } catch (error: any) {
      Alert.alert("拍照與裁切失敗", error.message);
    }
  };

  const handleStartMultiAnalyze = async () => {
    if (!isTestMode && capturedPhotos.length === 0) return;
    if (isLoading) return;
    setIsLoading(true);
    isProcessing.current = true;
    try {
      const targetBarcode = isTestMode ? "TEST" : (lastBarcode || "MANUAL_OCR");
      const result = await fetchFromFog(targetBarcode, userProfile, capturedPhotos);
      
      // 💾 儲存歷史紀錄
      StorageService.saveScanHistory(
        db, 
        result.product_info.barcode, 
        result.product_info.name, 
        result.final_health_diagnosis.score, 
        result.final_health_diagnosis.grade
      );
      await refreshHistory();

      setScanResult(result);
      setCurrentPage('result');
      setCapturedPhotos([]);
      setIsTestMode(false);
    } catch (error: any) {
      Alert.alert("分析失敗", error.message);
    } finally {
      setIsLoading(false);
      isProcessing.current = false;
    }
  };

  const handleBarcodeScanned = async ({ data }: { data: string }) => {
    if (isProcessing.current || scanned || scannerMode === 'photo') return;
    isProcessing.current = true;
    setScanned(true);
    setIsLoading(true);
    setLastBarcode(data);
    try {
      const result = await fetchFromFog(data, userProfile);
      
      // 💾 儲存歷史紀錄
      StorageService.saveScanHistory(
        db, 
        result.product_info.barcode, 
        result.product_info.name, 
        result.final_health_diagnosis.score, 
        result.final_health_diagnosis.grade
      );
      await refreshHistory();

      triggerFeedback('success');
      setScanResult(result);
      setCurrentPage('result');
    } catch (error: any) {
      if (error.message.includes('not found')) {
        Alert.alert("尚無產品", "是否拍攝成分表分析？", [
          { text: "取消", style: "cancel", onPress: () => { setCurrentPage('profile'); resetScanner(); }},
          { text: "拍照", onPress: () => { setScannerMode('photo'); resetScanner(); }}
        ]);
      } else {
        Alert.alert("分析失敗", error.message);
        resetScanner();
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleClearCache = async () => {
    Alert.alert(
      "清除 Fog 快取",
      "確定要清除所有已儲存的掃描快取嗎？這將強制下一次掃描向 Cloud 請求最新資料。",
      [
        { text: "取消", style: "cancel" },
        { 
          text: "確定清除", 
          style: "destructive",
          onPress: async () => {
            const success = await clearFogCache();
            if (success) {
              Alert.alert("成功", "Fog 節點快取已清除！");
            } else {
              Alert.alert("失敗", "無法連線至 Fog 節點，請檢查網路設定。");
            }
          }
        }
      ]
    );
  };

  const renderProfilePage = () => (
    <ScrollView style={{ flex: 1, backgroundColor: '#f8fafc' }} contentContainerStyle={{ padding: 20, paddingBottom: 100 }}>
      <View style={{ alignItems: 'center', marginTop: 40, marginBottom: 40 }}>
        <View style={{ width: 100, height: 100, backgroundColor: '#ecfdf5', borderRadius: 40, alignItems: 'center', justifyContent: 'center' }}>
          <UserIcon size={48} color="#059669" />
        </View>
        <Text style={{ fontSize: 32, fontWeight: '900', color: '#0f172a', marginTop: 20 }}>使用者背景</Text>
        <Text style={{ fontSize: 16, color: '#64748b', textAlign: 'center', marginTop: 10 }}>設定您的特定需求，AI 為您客製化診斷。</Text>
      </View>

      <View style={{ marginBottom: 40 }}>
        <Text style={{ fontSize: 14, fontWeight: '900', color: '#94a3b8', marginBottom: 16, letterSpacing: 1 }}>族群設定</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
          {['adult', 'pregnant', 'child', 'hypertension', 'diabetes'].map((g) => (
            <Pressable key={g} onPress={() => setUserProfile({ ...userProfile, group: g as any })} style={{ flex: 1, minWidth: '45%' }}>
              <View style={{ padding: 18, borderRadius: 24, borderWidth: 2, borderColor: userProfile.group === g ? '#10b981' : '#f1f5f9', backgroundColor: userProfile.group === g ? '#f0fdf4' : 'white' }}>
                <Text style={{ fontSize: 18, fontWeight: '700', textAlign: 'center', color: userProfile.group === g ? '#10b981' : '#475569' }}>
                  {g === 'adult' ? '成人' : g === 'pregnant' ? '孕婦' : g === 'child' ? '嬰幼兒' : g === 'hypertension' ? '高血壓' : '糖尿病'}
                </Text>
              </View>
            </Pressable>
          ))}
        </View>

        <Text style={{ fontSize: 14, fontWeight: '900', color: '#94a3b8', marginTop: 24, marginBottom: 12, letterSpacing: 1 }}>自訂其他族群 / 健康條件</Text>
        <View style={{ flexDirection: 'row', gap: 12 }}>
          <TextInput 
            style={{ flex: 1, backgroundColor: 'white', borderRadius: 20, borderWidth: 2, borderColor: '#f1f5f9', paddingHorizontal: 20, paddingVertical: 14, fontSize: 16, color: '#0f172a' }}
            placeholder="例如: 痛風、乳糖不耐"
            placeholderTextColor="#94a3b8"
            value={customConditionInput}
            onChangeText={setCustomConditionInput}
          />
          <Pressable 
            style={{ backgroundColor: '#10b981', borderRadius: 20, width: 56, height: 56, alignItems: 'center', justifyContent: 'center' }}
            onPress={() => {
              const val = customConditionInput.trim();
              if (val && !userProfile.custom_conditions?.includes(val)) {
                setUserProfile({ ...userProfile, custom_conditions: [...(userProfile.custom_conditions || []), val] });
                setCustomConditionInput('');
              }
            }}
          >
            <Text style={{ fontSize: 28, color: 'white', fontWeight: 'bold' }}>+</Text>
          </Pressable>
        </View>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 12 }}>
          {userProfile.custom_conditions?.map(c => (
            <Pressable key={c} onPress={() => {
              setUserProfile({ ...userProfile, custom_conditions: userProfile.custom_conditions?.filter(item => item !== c) });
            }} style={{ backgroundColor: '#ecfdf5', borderRadius: 16, paddingHorizontal: 16, paddingVertical: 8, flexDirection: 'row', alignItems: 'center', gap: 6, borderWidth: 1, borderColor: '#a7f3d0' }}>
              <Text style={{ color: '#059669', fontWeight: 'bold' }}>{c}</Text>
              <Text style={{ color: '#059669', fontSize: 12 }}>✕</Text>
            </Pressable>
          ))}
        </View>
      </View>

      <View style={{ marginBottom: 40 }}>
        <Text style={{ fontSize: 14, fontWeight: '900', color: '#94a3b8', marginBottom: 16, letterSpacing: 1 }}>過敏原設定 (台灣 11 大類)</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
          {['甲殼類', '芒果', '花生', '牛奶', '蛋', '堅果', '芝麻', '含麩質穀物', '大豆', '魚類', '亞硫酸鹽'].map((allergen) => {
            const isSelected = userProfile.allergens?.includes(allergen);
            return (
              <Pressable key={allergen} onPress={() => {
                const current = userProfile.allergens || [];
                const next = isSelected ? current.filter(a => a !== allergen) : [...current, allergen];
                setUserProfile({ ...userProfile, allergens: next });
              }} style={{ paddingHorizontal: 20, paddingVertical: 12, borderRadius: 20, borderWidth: 2, borderColor: isSelected ? '#ef4444' : '#f1f5f9', backgroundColor: isSelected ? '#fef2f2' : 'white' }}>
                <Text style={{ fontSize: 16, fontWeight: '700', color: isSelected ? '#ef4444' : '#64748b' }}>{allergen}</Text>
              </Pressable>
            );
          })}
        </View>

        <Text style={{ fontSize: 14, fontWeight: '900', color: '#94a3b8', marginTop: 24, marginBottom: 12, letterSpacing: 1 }}>自訂其他過敏原</Text>
        <View style={{ flexDirection: 'row', gap: 12 }}>
          <TextInput 
            style={{ flex: 1, backgroundColor: 'white', borderRadius: 20, borderWidth: 2, borderColor: '#f1f5f9', paddingHorizontal: 20, paddingVertical: 14, fontSize: 16, color: '#0f172a' }}
            placeholder="例如: 奇異果"
            placeholderTextColor="#94a3b8"
            value={customAllergenInput}
            onChangeText={setCustomAllergenInput}
          />
          <Pressable 
            style={{ backgroundColor: '#ef4444', borderRadius: 20, width: 56, height: 56, alignItems: 'center', justifyContent: 'center' }}
            onPress={() => {
              const val = customAllergenInput.trim();
              if (val && !userProfile.custom_allergens?.includes(val)) {
                setUserProfile({ ...userProfile, custom_allergens: [...(userProfile.custom_allergens || []), val] });
                setCustomAllergenInput('');
              }
            }}
          >
            <Text style={{ fontSize: 28, color: 'white', fontWeight: 'bold' }}>+</Text>
          </Pressable>
        </View>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 12 }}>
          {userProfile.custom_allergens?.map(a => (
            <Pressable key={a} onPress={() => {
              setUserProfile({ ...userProfile, custom_allergens: userProfile.custom_allergens?.filter(item => item !== a) });
            }} style={{ backgroundColor: '#fef2f2', borderRadius: 16, paddingHorizontal: 16, paddingVertical: 8, flexDirection: 'row', alignItems: 'center', gap: 6, borderWidth: 1, borderColor: '#fecaca' }}>
              <Text style={{ color: '#b91c1c', fontWeight: 'bold' }}>{a}</Text>
              <Text style={{ color: '#b91c1c', fontSize: 12 }}>✕</Text>
            </Pressable>
          ))}
        </View>
      </View>

      {/* 歷史紀錄區塊 (從 SQLite 讀取) */}
      {scanHistory.length > 0 && (
        <View style={{ marginBottom: 30 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <Text style={{ fontSize: 14, fontWeight: '900', color: '#94a3b8', letterSpacing: 1 }}>最近掃描歷史紀錄</Text>
            <Pressable onPress={async () => { await db.runAsync("DELETE FROM scan_history"); refreshHistory(); }}>
              <RNText style={{ fontSize: 14, color: '#ef4444', fontWeight: '700' }}>清除全部</RNText>
            </Pressable>
          </View>
          <View style={{ gap: 12 }}>
            {scanHistory.slice(0, 5).map((item, i) => (
              <View key={i} style={{ backgroundColor: 'white', padding: 18, borderRadius: 24, borderWidth: 1, borderColor: '#f1f5f9', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontSize: 16, fontWeight: '800', color: '#1e293b' }} numberOfLines={1}>{item.product_name}</Text>
                  <Text style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>{item.barcode} • {new Date(item.timestamp).toLocaleDateString()}</Text>
                </View>
                <View style={{ width: 44, height: 44, borderRadius: 14, backgroundColor: item.grade === 'A' ? '#f0fdf4' : item.grade === 'B' ? '#eff6ff' : '#fffbeb', alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: item.grade === 'A' ? '#10b98140' : item.grade === 'B' ? '#3b82f640' : '#f59e0b40' }}>
                  <Text style={{ fontSize: 18, fontWeight: '900', color: item.grade === 'A' ? '#10b981' : item.grade === 'B' ? '#3b82f6' : '#f59e0b' }}>{item.grade}</Text>
                </View>
              </View>
            ))}
          </View>
        </View>
      )}

      <Pressable style={{ backgroundColor: '#0f172a', paddingVertical: 22, borderRadius: 28, flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 12, marginBottom: 16 }} onPress={() => { setCurrentPage('scanner'); setScannerMode('barcode'); resetScanner(); }}>
        <ScanIcon size={24} color="white" /><Text style={{ color: 'white', fontWeight: '900', fontSize: 20 }}>啟動視覺掃描</Text>
      </Pressable>

      <Pressable style={{ backgroundColor: '#f1f5f9', paddingVertical: 18, borderRadius: 24, flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 8, borderWidth: 1, borderColor: '#e2e8f0', marginBottom: 12 }} onPress={() => { setIsTestMode(true); setScannerMode('photo'); setCurrentPage('scanner'); resetScanner(); }}>
        <BeakerIcon size={20} color="#475569" /><Text style={{ color: '#475569', fontWeight: '800', fontSize: 16 }}>進入功能測試模式 (強制 AI 解析)</Text>
      </Pressable>

      <Pressable 
        style={{ backgroundColor: '#fef2f2', paddingVertical: 18, borderRadius: 24, flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 8, borderWidth: 1, borderColor: '#fee2e2' }} 
        onPress={handleClearCache}
      >
        <TrashIcon size={20} color="#ef4444" /><Text style={{ color: '#ef4444', fontWeight: '800', fontSize: 16 }}>清除 Fog 節點快取資料</Text>
      </Pressable>
    </ScrollView>
  );

  const renderScannerPage = () => (
    <View style={{ flex: 1, backgroundColor: 'black' }}>
      <CameraView ref={cameraRef} style={{ flex: 1 }} onBarcodeScanned={scanned ? undefined : handleBarcodeScanned} />
      <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, justifyContent: 'center', alignItems: 'center' }}>
        <View style={{ width: 280, height: scannerMode === 'barcode' ? 180 : 350, borderWidth: 2, borderColor: scannerMode === 'barcode' ? '#10b981' : '#f59e0b', borderRadius: 24, borderStyle: scannerMode === 'barcode' ? 'solid' : 'dashed' }} />
        <Text style={{ color: 'white', marginTop: 32, fontSize: 16, fontWeight: '900' }}>{scannerMode === 'barcode' ? '對準食品條碼' : '對準成分表區域拍照'}</Text>
        <Pressable style={{ position: 'absolute', top: 60, left: 20, backgroundColor: 'rgba(0,0,0,0.6)', padding: 12, borderRadius: 16 }} onPress={() => { if(scannerMode==='photo') setScannerMode('barcode'); else setCurrentPage('profile'); }}>
          <BackIcon size={24} color="white" />
        </Pressable>

        {capturedPhotos.length > 0 && (
          <View style={{ position: 'absolute', bottom: 180, left: 0, right: 0, height: 90 }}>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 20, gap: 12 }}>
              {capturedPhotos.map((photo, index) => (
                <View key={index} style={{ width: 80, height: 80, borderRadius: 16, overflow: 'hidden', borderWidth: 2, borderColor: '#10b981', backgroundColor: '#000' }}>
                  <Image source={{ uri: `data:image/jpeg;base64,${photo}` }} style={{ width: '100%', height: '100%', opacity: 0.8 }} />
                  <Pressable onPress={() => removePhoto(index)} style={{ position: 'absolute', top: 4, right: 4, backgroundColor: 'rgba(239, 68, 68, 0.9)', borderRadius: 10, width: 22, height: 22, alignItems: 'center', justifyContent: 'center' }}>
                    <Text style={{ color: 'white', fontSize: 10, fontWeight: '900' }}>✕</Text>
                  </Pressable>
                </View>
              ))}
            </ScrollView>
          </View>
        )}

        {scannerMode === 'photo' && !isLoading && (
          <View style={{ position: 'absolute', bottom: 60, width: '100%', alignItems: 'center', gap: 20 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 30 }}>
              <Pressable onPress={handleCapturePhoto} style={{ width: 80, height: 80, backgroundColor: 'white', borderRadius: 40, alignItems: 'center', justifyContent: 'center' }}><CameraIcon /></Pressable>
              {capturedPhotos.length > 0 && (
                <Pressable onPress={handleStartMultiAnalyze} style={{ backgroundColor: '#10b981', paddingHorizontal: 24, paddingVertical: 16, borderRadius: 24, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <ZapIcon /><Text style={{ color: 'white', fontWeight: '900', fontSize: 16 }}>開始分析 ({capturedPhotos.length})</Text>
                </Pressable>
              )}
            </View>
          </View>
        )}
      </View>
      {isLoading && (
        <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(255,255,255,0.95)', justifyContent: 'center', alignItems: 'center' }}>
          <LottieView autoPlay style={{ width: 200, height: 200 }} source={{ uri: 'https://assets5.lottiefiles.com/packages/lf20_6n0m8z.json' }} />
          <Text style={{ color: '#0f172a', fontWeight: '900', marginTop: 20, fontSize: 18, letterSpacing: 1 }}>AI 專家診斷中...</Text>
        </View>
      )}
    </View>
  );

  const renderResultPage = () => {
    if (!scanResult) return null;
    const chemical = (scanResult.ingredients_detail || []).filter(i => i.isAdditive);
    const nutrition = scanResult.nutrition_facts;
    const diagnosis = scanResult.final_health_diagnosis;
    const rawMarks = scanResult.certification_marks || [];
    // 強化匹配邏輯：確保 TQF, CAS 等關鍵字能正確對應
    const matchedMarks = Object.keys(MARK_DEFINITIONS).filter(key => 
      rawMarks.some(rm => 
        (typeof rm === 'string' ? rm : (rm as any).name || "").toUpperCase().includes(key.toUpperCase())
      )
    );

    return (
      <View style={{ flex: 1, backgroundColor: '#f8fafc' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: 20, paddingTop: 60, paddingBottom: 20, backgroundColor: 'white', borderBottomWidth: 1, borderBottomColor: '#f1f5f9' }}>
          <Pressable onPress={() => setCurrentPage('profile')}><BackIcon size={24} color="#64748b" /></Pressable>
          <View style={{ flex: 1, alignItems: 'center' }}><Text style={{ fontSize: 12, fontWeight: '900', color: '#94a3b8', letterSpacing: 2 }}>FOODAWARE PRO v2.0</Text></View>
          <View style={{ width: 30 }} />
        </View>

        <ScrollView contentContainerStyle={{ padding: 20, paddingBottom: 100 }}>
          <View style={{ backgroundColor: 'white', padding: 28, borderRadius: 44, alignItems: 'center', marginBottom: 20 }}>
            <Text style={{ fontSize: 13, fontWeight: '900', color: '#94a3b8', letterSpacing: 2 }}>{scanResult.product_info.brand}</Text>
            <Text style={{ fontSize: 28, fontWeight: '900', color: '#1e293b', textAlign: 'center', marginBottom: 12 }}>{scanResult.product_info.name}</Text>
            <ScoreGauge score={diagnosis.score} grade={diagnosis.grade} />
            {matchedMarks.length > 0 && (
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 24, justifyContent: 'center' }}>
                {matchedMarks.map((m: string) => {
                  const info = MARK_DEFINITIONS[m];
                  return (
                    <Pressable key={m} onPress={() => Alert.alert(info.title, info.desc)} style={{ backgroundColor: info.bgColor, paddingHorizontal: 16, paddingVertical: 8, borderRadius: 14, borderWidth: 1, borderColor: info.color + '40' }}>
                      <Text style={{ fontSize: 13, fontWeight: '900', color: info.textColor }}>{m}</Text>
                    </Pressable>
                  );
                })}
              </View>
            )}
          </View>

          {/* 個人化診斷總結 (拆分顯示) */}
          <View style={{ backgroundColor: '#0f172a', padding: 28, borderRadius: 36, marginBottom: 20, shadowColor: "#000", shadowOffset: { width: 0, height: 10 }, shadowOpacity: 0.3, shadowRadius: 20 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, opacity: 0.6, marginBottom: 16 }}>
              <InfoIcon size={16} color="white" /><Text style={{ fontSize: 12, fontWeight: '900', color: 'white', letterSpacing: 1 }}>AI EXPERT DIAGNOSIS</Text>
            </View>
            
            {/* 根據 Fog 的格式拆分摘要 */}
            {(() => {
              const fullSummary = diagnosis.summary || "";
              const parts = fullSummary.split('[AI 深度分析]：');
              const personalAdvice = parts[0]?.trim() || "正在分析您的個人健康配對...";
              const deepAnalysis = parts[1]?.trim();

              return (
                <View>
                  <Text style={{ fontSize: 18, fontWeight: '700', color: '#10b981', lineHeight: 28, marginBottom: deepAnalysis ? 20 : 0 }}>
                    {personalAdvice}
                  </Text>
                  {deepAnalysis && (
                    <View style={{ borderTopWidth: 1, borderTopColor: 'rgba(255,255,255,0.1)', paddingTop: 20 }}>
                      <Text style={{ fontSize: 15, fontWeight: '500', color: 'rgba(255,255,255,0.8)', lineHeight: 24 }}>
                        {deepAnalysis}
                      </Text>
                    </View>
                  )}
                </View>
              );
            })()}

            {diagnosis.warnings && diagnosis.warnings.length > 0 && (
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 24 }}>
                {diagnosis.warnings.map((w, i) => (
                  <View key={i} style={{ backgroundColor: 'rgba(239, 68, 68, 0.2)', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: 'rgba(239, 68, 68, 0.3)' }}>
                    <Text style={{ fontSize: 12, fontWeight: '900', color: '#fca5a5' }}>⚠️ {w}</Text>
                  </View>
                ))}
              </View>
            )}
          </View>

          {/* 顯示個人化評分依據 (從 SQLite 暫存背景後的計算結果) */}
          {diagnosis.score_breakdown && diagnosis.score_breakdown.length > 0 && (
            <View style={{ backgroundColor: 'white', padding: 28, borderRadius: 36, marginBottom: 20 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 24 }}>
                <View style={{ width: 36, height: 36, backgroundColor: '#eef2ff', borderRadius: 14, alignItems: 'center', justifyContent: 'center' }}><ShieldIcon /></View>
                <Text style={{ fontSize: 18, fontWeight: '900', color: '#1e293b' }}>Fog 個人化運算邏輯</Text>
              </View>

              {diagnosis.score_breakdown.map((item: any, i: number) => (
                <View key={i} style={{ marginBottom: 24, borderBottomWidth: i === diagnosis.score_breakdown.length - 1 ? 0 : 1, borderBottomColor: '#f8fafc', paddingBottom: i === diagnosis.score_breakdown.length - 1 ? 0 : 20 }}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, flex: 1 }}>
                      <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: item.points >= 0 ? '#10b981' : '#ef4444' }} />
                      <Text style={{ fontSize: 15, fontWeight: '800', color: '#334155' }}>{item.reason}</Text>
                    </View>
                    <Text style={{ fontSize: 15, fontWeight: '900', color: item.points >= 0 ? '#10b981' : '#ef4444', tabularNums: true }}>
                      {item.points >= 0 ? `+${item.points}` : item.points}
                    </Text>
                  </View>
                  {item.description && (
                    <Text style={{ fontSize: 13, color: '#94a3b8', lineHeight: 20, marginLeft: 18 }}>{item.description}</Text>
                  )}
                </View>
              ))}

              <View style={{ marginTop: 12, paddingTop: 20, borderTopWidth: 2, borderTopColor: '#f1f5f9', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                <Text style={{ fontSize: 14, fontWeight: '900', color: '#64748b', letterSpacing: 1 }}>最終診斷總分</Text>
                <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 4 }}>
                  <Text style={{ fontSize: 32, fontWeight: '900', color: '#0f172a' }}>{diagnosis.score}</Text>
                  <Text style={{ fontSize: 14, fontWeight: '700', color: '#cbd5e1' }}>/ 100</Text>
                </View>
              </View>
            </View>
          )}

          {/* 營養成分摘要 */}
          <View style={{ backgroundColor: 'white', padding: 24, borderRadius: 32, marginBottom: 20 }}>
            <Text style={{ fontSize: 18, fontWeight: '900', color: '#1e293b', marginBottom: 20 }}>營養成分摘要</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 14 }}>
              <View style={{ flex: 1, minWidth: '28%', backgroundColor: '#f8fafc', padding: 16, borderRadius: 20, alignItems: 'center' }}>
                <Text style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>熱量</Text>
                <Text style={{ fontSize: 18, fontWeight: '900', color: '#1e293b' }}>{nutrition.calories}</Text>
                <Text style={{ fontSize: 10, color: '#94a3b8' }}>kcal</Text>
              </View>
              <View style={{ flex: 1, minWidth: '28%', backgroundColor: '#f8fafc', padding: 16, borderRadius: 20, alignItems: 'center' }}>
                <Text style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>蛋白質</Text>
                <Text style={{ fontSize: 18, fontWeight: '900', color: '#1e293b' }}>{nutrition.protein}</Text>
                <Text style={{ fontSize: 10, color: '#94a3b8' }}>g</Text>
              </View>
              <View style={{ flex: 1, minWidth: '28%', backgroundColor: '#f8fafc', padding: 16, borderRadius: 20, alignItems: 'center' }}>
                <Text style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>脂肪</Text>
                <Text style={{ fontSize: 18, fontWeight: '900', color: '#1e293b' }}>{nutrition.fat}</Text>
                <Text style={{ fontSize: 10, color: '#94a3b8' }}>g</Text>
              </View>
              <View style={{ flex: 1, minWidth: '28%', backgroundColor: '#f8fafc', padding: 16, borderRadius: 20, alignItems: 'center' }}>
                <Text style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>碳水</Text>
                <Text style={{ fontSize: 18, fontWeight: '900', color: '#1e293b' }}>{nutrition.carbohydrates}</Text>
                <Text style={{ fontSize: 10, color: '#94a3b8' }}>g</Text>
              </View>
              <View style={{ flex: 1, minWidth: '28%', backgroundColor: '#f8fafc', padding: 16, borderRadius: 20, alignItems: 'center' }}>
                <Text style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>糖</Text>
                <Text style={{ fontSize: 18, fontWeight: '900', color: '#1e293b' }}>{nutrition.sugar}</Text>
                <Text style={{ fontSize: 10, color: '#94a3b8' }}>g</Text>
              </View>
              <View style={{ flex: 1, minWidth: '28%', backgroundColor: '#f8fafc', padding: 16, borderRadius: 20, alignItems: 'center' }}>
                <Text style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>鈉</Text>
                <Text style={{ fontSize: 18, fontWeight: '900', color: '#1e293b' }}>{nutrition.sodium}</Text>
                <Text style={{ fontSize: 10, color: '#94a3b8' }}>mg</Text>
              </View>
            </View>
          </View>

          {/* 台灣食品標章認證 */}
          {matchedMarks.length > 0 && (
            <View style={{ backgroundColor: 'white', padding: 28, borderRadius: 36, marginBottom: 20 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 24 }}>
                <View style={{ width: 36, height: 36, backgroundColor: '#f0fdf4', borderRadius: 14, alignItems: 'center', justifyContent: 'center' }}><ShieldIcon size={18} color="#059669" /></View>
                <Text style={{ fontSize: 18, fontWeight: '900', color: '#1e293b' }}>通過台灣食品標章認證</Text>
              </View>

              <View style={{ gap: 14 }}>
                {matchedMarks.map((m: string) => {
                  const info = MARK_DEFINITIONS[m];
                  return (
                    <Pressable 
                      key={m} 
                      onPress={() => Alert.alert(info.title, info.desc)}
                      style={{ 
                        backgroundColor: info.bgColor, 
                        padding: 20, 
                        borderRadius: 28, 
                        borderWidth: 2, 
                        borderColor: info.color + '20',
                        flexDirection: 'row',
                        alignItems: 'center',
                        gap: 16
                      }}
                    >
                      <View style={{ width: 52, height: 52, borderRadius: 18, backgroundColor: 'white', alignItems: 'center', justifyContent: 'center', shadowColor: info.color, shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.1, shadowRadius: 8 }}>
                        <Text style={{ fontSize: 24 }}>{m === 'CAS' ? '🥩' : m === 'TQF' ? '🏢' : m === 'TAP' ? '🥬' : m === '健康食品' ? '🧬' : '🌱'}</Text>
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={{ fontSize: 16, fontWeight: '900', color: info.textColor }}>{info.title}</Text>
                        <Text style={{ fontSize: 12, color: info.textColor + 'cc', marginTop: 4 }} numberOfLines={1}>{info.desc}</Text>
                      </View>
                    </Pressable>
                  );
                })}
              </View>
            </View>
          )}

          {/* 廠商食安歷史警訊 (補回) */}
          {scanResult.manufacturer_alerts && scanResult.manufacturer_alerts.length > 0 && (
            <View style={{ backgroundColor: '#fef2f2', padding: 28, borderRadius: 36, marginBottom: 20, borderWidth: 1, borderColor: '#fee2e2' }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 20 }}>
                <View style={{ width: 36, height: 36, backgroundColor: '#fee2e2', borderRadius: 14, alignItems: 'center', justifyContent: 'center' }}>
                  <WarningIcon size={18} color="#991b1b" />
                </View>
                <Text style={{ fontSize: 18, fontWeight: '900', color: '#991b1b' }}>廠商食安歷史警訊</Text>
              </View>
              
              <View style={{ gap: 14 }}>
                {scanResult.manufacturer_alerts.map((alert: any, i: number) => (
                  <View key={i} style={{ backgroundColor: 'white', padding: 20, borderRadius: 24, borderWidth: 1, borderColor: '#fee2e2' }}>
                    <Text style={{ fontSize: 12, fontWeight: '900', color: '#ef4444', marginBottom: 6 }}>{alert.alert_date}</Text>
                    <Text style={{ fontSize: 16, fontWeight: '800', color: '#1e293b', marginBottom: 8 }}>{alert.title}</Text>
                    <Text style={{ fontSize: 13, color: '#64748b', lineHeight: 20 }}>{alert.content}</Text>
                  </View>
                ))}
              </View>
            </View>
          )}

          <View style={{ gap: 14 }}>
            <Pressable onPress={() => setExpandChemical(!expandChemical)} style={{ backgroundColor: expandChemical ? '#fffbeb' : 'white', borderRadius: 28, padding: 24 }}>
              <Text style={{ fontSize: 18, fontWeight: '900', color: '#1e293b' }}>化學/添加物分析 ({chemical.length})</Text>
              {expandChemical && <View style={{ marginTop: 20, gap: 12 }}>{chemical.map((ing, i) => <IngredientCard key={i} ingredient={ing} />)}</View>}
            </Pressable>
          </View>
        </ScrollView>
      </View>
    );
  };

  return (
    <View style={{ flex: 1, backgroundColor: 'white' }}>
      <StatusBar barStyle="dark-content" />
      {currentPage === 'profile' && renderProfilePage()}
      {currentPage === 'scanner' && renderScannerPage()}
      {currentPage === 'result' && renderResultPage()}
    </View>
  );
}

export default function App() {
  return (
    <SQLite.SQLiteProvider databaseName="foodaware.db">
      <AppContent />
    </SQLite.SQLiteProvider>
  );
}

registerRootComponent(App);
