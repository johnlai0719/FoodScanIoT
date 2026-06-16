import React, { useState, useEffect } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  View,
  Text,
  ScrollView,
  TextInput,
  Pressable,
  ActivityIndicator,
  SafeAreaView,
  KeyboardAvoidingView,
  Platform,
  LayoutAnimation,
  UIManager,
  StyleSheet,
  Modal,
} from 'react-native';
import { Sparkles, Sliders, Database, ShoppingBag, AlertOctagon, RotateCcw, Layers, ShieldCheck, Grid, ChevronRight, Zap, ChevronUp, ChevronDown, ArrowLeft, AlertTriangle, CheckCircle2, Info, ScanLine, Keyboard, Activity, Settings2, X, Trash2 } from 'lucide-react-native';

import { useFontScale } from '../contexts/FontScaleContext';
import { UserConditions, AnalysisResponse } from '../types';
import { MOCK_RESULTS } from '../mockResultData';
import Gauge from '../components/Gauge';
import IngredientsList from '../components/IngredientsList';
import DropdownEvent from '../components/DropdownEvent';
import PackageImageScanner from '../components/PackageImageScanner';
import BarcodeScanner from '../components/BarcodeScanner';
import {
  getScoreBreakdownList,
  getDynamicHealthPoints,
  getDynamicHealthSegments,
  getDynamicHealthLegend,
} from '../utils/scoring';
import {
  getAiProductSummary,
  getAiAdditivesSummary,
  getAiHistorySummary,
} from '../utils/aiSummaries';

if (Platform.OS === 'android') {
  UIManager.setLayoutAnimationEnabledExperimental?.(true);
}

// ─── Constants ────────────────────────────────────────────────────────────────
const TAIWAN_ALLERGENS = [
  { key: '甲殼類', name: '甲殼類' },
  { key: '芒果', name: '芒果' },
  { key: '花生', name: '花生' },
  { key: '牛奶', name: '牛奶' },
  { key: '蛋', name: '蛋' },
  { key: '堅果', name: '堅果' },
  { key: '芝麻', name: '芝麻' },
  { key: '含麩質穀物', name: '含麩質穀物' },
  { key: '大豆', name: '大豆' },
  { key: '魚類', name: '魚類' },
  { key: '亞硫酸鹽', name: '亞硫酸鹽' },
];

const GROUPS: { value: UserConditions['group']; label: string }[] = [
  { value: 'adult', label: '一般成人' },
  { value: 'pregnant', label: '孕婦 / 哺乳' },
  { value: 'child', label: '幼童' },
];

const CHRONIC_DISEASES = [
  { key: 'hypertension', label: '高血壓' },
  { key: 'diabetes', label: '糖尿病' },
];


// ─── Component ────────────────────────────────────────────────────────────────
export default function HomeScreen() {
  const { fontScale, toggleFontScale, isLarge } = useFontScale();
  const s = createStyles(fontScale);

  const [currentStep, setCurrentStep] = useState<1 | 2 | 3>(1);
  const [targetGroup, setTargetGroup] = useState<UserConditions['group']>('adult');
  const [allergens, setAllergens] = useState<string[]>([]);
  const [customGroup, setCustomGroup] = useState('');
  const [customAllergen, setCustomAllergen] = useState('');
  const [chronicDiseases, setChronicDiseases] = useState<string[]>([]);
  const [customChronic, setCustomChronic] = useState('');

  useEffect(() => {
    AsyncStorage.multiGet(['customGroup', 'customAllergen', 'chronicDiseases', 'customChronic', 'allergens']).then(pairs => {
      pairs.forEach(([key, value]) => {
        if (!value) return;
        if (key === 'customGroup') setCustomGroup(value);
        if (key === 'customAllergen') setCustomAllergen(value);
        if (key === 'chronicDiseases') setChronicDiseases(JSON.parse(value));
        if (key === 'customChronic') setCustomChronic(value);
        if (key === 'allergens') setAllergens(JSON.parse(value));
      });
    });
  }, []);

  const saveCustomGroup = (text: string) => {
    setCustomGroup(text);
    AsyncStorage.setItem('customGroup', text);
  };
  const saveCustomAllergen = (text: string) => {
    setCustomAllergen(text);
    AsyncStorage.setItem('customAllergen', text);
  };
  const toggleChronic = (key: string) => {
    setChronicDiseases(prev => {
      const next = prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key];
      AsyncStorage.setItem('chronicDiseases', JSON.stringify(next));
      return next;
    });
  };
  const saveCustomChronic = (text: string) => {
    setCustomChronic(text);
    AsyncStorage.setItem('customChronic', text);
  };
  const [barcodeInput, setBarcodeInput] = useState('');
  const [uploadedImages, setUploadedImages] = useState<string[]>([]);
  const [serverEndpoint, setServerEndpoint] = useState<'fog' | 'cloud'>('fog');
  const [clearCacheStatus, setClearCacheStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');

  const [inputMode, setInputMode] = useState<'manual' | 'scan'>('scan');
  const [advancedVisible, setAdvancedVisible] = useState(false);
  const [scannerVisible, setScannerVisible] = useState(false);

  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisResult, setAnalysisResult] = useState<AnalysisResponse | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [activeDetailView, setActiveDetailView] = useState<'additives' | 'history' | 'breakdown' | null>(null);
  const [isScoreExpanded, setIsScoreExpanded] = useState(false);

  // ── Handlers ──────────────────────────────────────────────────────────────
  const toggleAllergen = (key: string) => {
    setAllergens(prev => {
      const next = prev.includes(key) ? prev.filter(a => a !== key) : [...prev, key];
      AsyncStorage.setItem('allergens', JSON.stringify(next));
      return next;
    });
  };

  const handleQuickPreview = (code: string) => {
    const data = MOCK_RESULTS[code] ?? MOCK_RESULTS['4710018123456'];
    setBarcodeInput(code);
    setAnalysisResult(data);
    setAnalysisError(null);
    setIsAnalyzing(false);
    setCurrentStep(3);
  };

  const handleClearFogCache = async () => {
    setClearCacheStatus('loading');
    try {
      const res = await fetch('http://100.86.249.39:3001/cache/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      setClearCacheStatus(res.ok ? 'success' : 'error');
    } catch {
      setClearCacheStatus('error');
    } finally {
      setTimeout(() => setClearCacheStatus('idle'), 2500);
    }
  };

  const handleSubmitAnalysis = async () => {
    if (!barcodeInput && uploadedImages.length === 0) return;

    // Fast-path for known mock barcodes
    if (uploadedImages.length === 0 && MOCK_RESULTS[barcodeInput]) {
      setAnalysisResult(MOCK_RESULTS[barcodeInput]);
      setAnalysisError(null);
      setCurrentStep(3);
      return;
    }

    setIsAnalyzing(true);
    setAnalysisResult(null);
    setAnalysisError(null);
    setCurrentStep(3);

    try {
      const API_URL = serverEndpoint === 'fog'
        ? 'http://100.86.249.39:3001/query'
        : 'http://100.119.217.100:3003/api/analyze';
      const response = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-cache' },
        body: JSON.stringify({
          barcode: barcodeInput,
          label_images: uploadedImages,
          user_conditions: {
            group: targetGroup,
            allergens: [...allergens, ...(customAllergen.trim() ? [customAllergen.trim()] : [])],
            chronic_conditions: [
              ...chronicDiseases,
              ...(customChronic.trim() ? [customChronic.trim()] : []),
            ],
          },
        }),
      });
      if (!response.ok) throw new Error(`伺服器代碼: ${response.status}`);
      const json = await response.json();
      // Fog 可能將結果包在 data 欄位內
      const result = json.health_score !== undefined ? json : (json.data ?? json);

      // ── Debug：記錄 Fog 回傳中缺少的欄位 ──────────────────────────────
      const EXPECTED_FIELDS: (keyof typeof result)[] = [
        'health_score', 'risk_level', 'cached',
        'product_info', 'allergen_warnings', 'score_breakdown',
        'ingredients_detail', 'food_safety_events',
        'overall_summary', 'additives_summary', 'safety_events_summary',
      ];
      const missing = EXPECTED_FIELDS.filter(f => result[f] == null);
      const nulled  = EXPECTED_FIELDS.filter(f => f in result && result[f] === null);
      if (missing.length > 0) console.warn('[Fog] 缺少欄位:', missing.join(', '));
      if (nulled.length > 0)  console.warn('[Fog] null 欄位:', nulled.join(', '));
      console.log('[Fog] 收到欄位:', Object.keys(result).join(', '));
      console.log('[Fog] cached:', result.cached, '| score:', result.health_score, '| ingredients:', result.ingredients_detail?.length ?? 'null');
      console.log('[Fog] score_breakdown:', JSON.stringify(result.score_breakdown?.slice(0, 5)));
      // ────────────────────────────────────────────────────────────────────

      setAnalysisResult(result);
    } catch (err: any) {
      setAnalysisError(err.message ?? '無法連線至後端分析節點，請確認伺服器有正常運作！');
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleBackToScan = () => {
    setCurrentStep(2);
    setAnalysisResult(null);
    setAnalysisError(null);
    setActiveDetailView(null);
    setIsScoreExpanded(false);
  };

  // ── Derived stats ──────────────────────────────────────────────────────────
  const allIngredients = analysisResult?.ingredients_detail ?? [];
  const safeAllergenWarnings = analysisResult?.allergen_warnings ?? [];
  const safeFoodSafetyEvents = analysisResult?.food_safety_events ?? [];
  const totalAdditivesCount = allIngredients.filter(i => i.isAdditive === true || i.isAdditive === 'true').length;
  const highRiskCount = allIngredients.filter(i => {
    if (!(i.isAdditive === true || i.isAdditive === 'true')) return false;
    return (i.groupRisks ?? []).some(r => r.riskLevel >= 3);
  }).length;

  const scoreBreakdownList = getScoreBreakdownList(analysisResult);

  // 後端個人化命中（格式：偵測到過敏原：XXX）
  const backendMatchedAllergens = (analysisResult?.allergen_warnings ?? [])
    .filter(w => /偵測到過敏原[：:]/.test(w))
    .map(w => w.replace(/.*偵測到過敏原[：:]\s*/, '').trim())
    .filter(Boolean)
    .filter(a => allergens.length === 0 || allergens.some(sel => a.includes(sel) || sel.includes(a)));

  // 通用警告（產品本身標示，非個人化命中）
  const generalAllergenWarnings = safeAllergenWarnings
    .filter(w => !/偵測到過敏原[：:]/.test(w));

  // Fallback：後端未做個人化比對時，APP 自行掃描警語文字是否含用戶選取的過敏原關鍵字
  const fallbackMatchedAllergens = backendMatchedAllergens.length === 0 && allergens.length > 0
    ? allergens.filter(sel => generalAllergenWarnings.some(w => w.includes(sel)))
    : [];

  const matchedAllergens = backendMatchedAllergens.length > 0
    ? backendMatchedAllergens
    : fallbackMatchedAllergens;
  const { pos: dynamicPos, neg: dynamicNeg } = getDynamicHealthPoints(analysisResult, scoreBreakdownList);
  const healthSegments = getDynamicHealthSegments(analysisResult, scoreBreakdownList);
  const healthLegend = getDynamicHealthLegend(scoreBreakdownList);
  const scoreValue = analysisResult?.health_score ?? 100;

  // 根據使用者個人資料（群體＋慢性病）篩選有風險的添加物
  const userGroupKeys = [
    ...(targetGroup !== 'adult' ? [targetGroup] : []),
    ...chronicDiseases,
  ];
  const personalAdditiveRisks = allIngredients
    .filter(i => i.isAdditive === true || i.isAdditive === 'true')
    .flatMap(ing =>
      (ing.groupRisks ?? [])
        .filter(r => userGroupKeys.includes(r.group))
        .map(r => ({ name: ing.name, reason: r.reason, group: r.group }))
    );

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={s.root}>
      <Pressable onPress={toggleFontScale} style={[s.fontScaleBtn, isLarge && s.fontScaleBtnActive]}>
        <Text style={[s.fontScaleBtnText, isLarge && s.fontScaleBtnTextActive]}>Aa</Text>
      </Pressable>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView contentContainerStyle={s.scroll} keyboardShouldPersistTaps="handled">

          {/* ══════════ STEP 1: Health Profile ══════════ */}
          {currentStep === 1 && (
            <View style={s.card}>
              <View style={s.cardHeader}>
                <Sliders size={18} color="#757575" />
                <Text style={s.cardTitle}>設定個人健康特徵</Text>
              </View>
              <Text style={s.cardSubtitle}>建立受檢者的健康資料，系統將依此提供個人化風險警告</Text>

              {/* Group selector */}
              <Text style={s.sectionLabel}>1. 群體</Text>
              <View style={s.groupGrid}>
                {GROUPS.map(g => (
                  <Pressable
                    key={g.value}
                    onPress={() => setTargetGroup(g.value)}
                    style={[s.groupBtn, targetGroup === g.value && s.groupBtnActive]}
                  >
                    <Text style={[s.groupBtnText, targetGroup === g.value && s.groupBtnTextActive]}>
                      {g.label}
                    </Text>
                  </Pressable>
                ))}
              </View>
              <TextInput
                value={customGroup}
                onChangeText={saveCustomGroup}
                placeholder="自定義（選填）"
                placeholderTextColor="#94A3B8"
                style={s.customInlineInput}
              />

              {/* Chronic diseases */}
              <Text style={[s.sectionLabel, { marginTop: 20 }]}>2. 慢性病</Text>
              <View style={s.groupGrid}>
                {CHRONIC_DISEASES.map(d => {
                  const checked = chronicDiseases.includes(d.key);
                  return (
                    <Pressable
                      key={d.key}
                      onPress={() => toggleChronic(d.key)}
                      style={[s.allergenBtn, checked && s.allergenBtnActive]}
                    >
                      <Text style={[s.allergenText, checked && s.allergenTextActive]}>{d.label}</Text>
                    </Pressable>
                  );
                })}
              </View>
              <TextInput
                value={customChronic}
                onChangeText={saveCustomChronic}
                placeholder="自定義（選填，例如：慢性腎臟病）"
                placeholderTextColor="#94A3B8"
                style={s.customInlineInput}
              />

              {/* Allergen toggles */}
              <Text style={[s.sectionLabel, { marginTop: 20 }]}>3. 過敏原</Text>
              <View style={s.allergenGrid}>
                {TAIWAN_ALLERGENS.map(a => {
                  const checked = allergens.includes(a.key);
                  return (
                    <Pressable
                      key={a.key}
                      onPress={() => toggleAllergen(a.key)}
                      style={[s.allergenBtn, checked && s.allergenBtnActive]}
                    >
                      <Text style={[s.allergenText, checked && s.allergenTextActive]}>{a.name}</Text>
                    </Pressable>
                  );
                })}
              </View>
              <TextInput
                value={customAllergen}
                onChangeText={saveCustomAllergen}
                placeholder="自定義過敏原（選填，例如：木瓜）"
                placeholderTextColor="#94A3B8"
                style={s.customInlineInput}
              />

              {/* Action buttons */}
              <View style={[s.row, { marginTop: 24 }]}>
                <Pressable style={[s.btnPrimary, { flex: 1 }]} onPress={() => setCurrentStep(2)}>
                  <Text style={s.btnPrimaryText}>下一步</Text>
                  <ChevronRight size={14} color="#fff" />
                </Pressable>
              </View>
              {__DEV__ && (
                <Pressable style={[s.btnPrimary, { marginTop: 10 }]} onPress={() => handleQuickPreview('4710018123456')}>
                  <Zap size={14} color="#fef3c7" />
                  <Text style={s.btnPrimaryText}>載入測試：即刻生成範例儀表板</Text>
                </Pressable>
              )}
            </View>
          )}

          {/* ══════════ STEP 2: Scan ══════════ */}
          {currentStep === 2 && (
            <>
              <BarcodeScanner
                visible={scannerVisible}
                onClose={() => setScannerVisible(false)}
                onBarcodeScanned={code => {
                  setBarcodeInput(code);
                  setScannerVisible(false);
                }}
                onPhotosSubmit={photos => {
                  setUploadedImages(prev => [...prev, ...photos]);
                  setScannerVisible(false);
                }}
              />

              <View style={s.card}>
                <View style={s.cardHeader}>
                  <Database size={18} color="#757575" />
                  <Text style={s.cardTitle}>上傳包裝照片組</Text>
                </View>
                <Text style={s.cardSubtitle}>提供清晰成分或條碼標籤特寫，系統將啟動 Gemini 精準解析</Text>

                {/* Input mode toggle */}
                <Text style={[s.sectionLabel, { marginTop: 16 }]}>條碼輸入方式</Text>
                <View style={s.inputModeRow}>
                  <Pressable
                    style={[s.inputModeBtn, inputMode === 'manual' && s.inputModeBtnActive]}
                    onPress={() => setInputMode('manual')}
                  >
                    <Keyboard size={15} color={inputMode === 'manual' ? SEL_TEXT : BRAND} />
                    <Text style={[s.inputModeBtnText, inputMode === 'manual' && s.inputModeBtnTextActive]}>
                      手動輸入
                    </Text>
                  </Pressable>
                  <Pressable
                    style={[s.inputModeBtn, inputMode === 'scan' && s.inputModeBtnActive]}
                    onPress={() => setInputMode('scan')}
                  >
                    <ScanLine size={15} color={inputMode === 'scan' ? SEL_TEXT : BRAND} />
                    <Text style={[s.inputModeBtnText, inputMode === 'scan' && s.inputModeBtnTextActive]}>
                      掃描條碼
                    </Text>
                  </Pressable>
                </View>

                {/* Manual input */}
                {inputMode === 'manual' && (
                  <>
                    <View style={[s.row, { marginTop: 10 }]}>
                      <TextInput
                        value={barcodeInput}
                        onChangeText={setBarcodeInput}
                        placeholder="請輸入條碼編號例如 4710018123456"
                        placeholderTextColor="#94A3B8"
                        style={s.textInput}
                        keyboardType="numeric"
                      />
                      {__DEV__ && (
                        <Pressable style={s.sampleBtn} onPress={() => setBarcodeInput('TEST')}>
                          <Text style={s.sampleBtnText}>測試碼</Text>
                        </Pressable>
                      )}
                    </View>
                    {__DEV__ && (
                      <View style={[s.row, { flexWrap: 'wrap', gap: 6, marginTop: 8 }]}>
                        <Text style={s.demoLabel}>Demo 快速預載：</Text>
                        {['4710018123456', 'TEST'].map(code => (
                          <Pressable key={code} style={s.demoChip} onPress={() => setBarcodeInput(code)}>
                            <Text style={s.demoChipText}>{code}</Text>
                          </Pressable>
                        ))}
                      </View>
                    )}
                  </>
                )}

                {/* Scan mode */}
                {inputMode === 'scan' && (
                  <View style={{ marginTop: 10, gap: 10 }}>
                    <Pressable style={s.scanLaunchBtn} onPress={() => setScannerVisible(true)}>
                      <View style={s.scanLaunchIcon}>
                        <ScanLine size={22} color={BRAND} />
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={s.scanLaunchTitle}>開啟條碼掃描器</Text>
                        <Text style={s.scanLaunchSub}>對準食品條碼自動讀取，或切換至拍照模式上傳標籤</Text>
                      </View>
                      <ChevronRight size={16} color={TEXT_MID} />
                    </Pressable>
                    {barcodeInput !== '' && (
                      <View style={s.scannedResult}>
                        <CheckCircle2 size={13} color={GREEN} />
                        <Text style={s.scannedResultText}>已掃描：{barcodeInput}</Text>
                        <Pressable onPress={() => setBarcodeInput('')}>
                          <Text style={s.scannedClear}>清除</Text>
                        </Pressable>
                      </View>
                    )}
                  </View>
                )}

                {/* Photo upload */}
                <Text style={[s.sectionLabel, { marginTop: 20 }]}>食品包裝標籤照片</Text>
                <PackageImageScanner
                  onImageCaptured={base64 => setUploadedImages(prev => [...prev, base64])}
                  images={uploadedImages}
                  onRemoveImage={i => setUploadedImages(prev => prev.filter((_, idx) => idx !== i))}
                />

                {/* Advanced settings entry */}
                <Pressable style={s.advancedBtn} onPress={() => setAdvancedVisible(true)}>
                  <Settings2 size={13} color={TEXT_MID} />
                  <Text style={s.advancedBtnText}>進階設定</Text>
                  <View style={s.advancedEndpointBadge}>
                    <Text style={s.advancedEndpointBadgeText}>
                      {serverEndpoint === 'fog' ? 'Fog 節點' : 'Cloud 節點'}
                    </Text>
                  </View>
                  <ChevronRight size={13} color={TEXT_MID} />
                </Pressable>

                {/* Nav buttons */}
                <View style={[s.row, { marginTop: 10, gap: 10 }]}>
                  <Pressable style={[s.btnSecondary, { flex: 1 }]} onPress={() => setCurrentStep(1)}>
                    <ArrowLeft size={14} color="#757575" />
                    <Text style={s.btnSecondaryText}>返回</Text>
                  </Pressable>
                  <Pressable
                    style={[s.btnPrimary, { flex: 2 }]}
                    onPress={handleSubmitAnalysis}
                    disabled={!barcodeInput && uploadedImages.length === 0}
                  >
                    <Text style={s.btnPrimaryText}>開始深入分析</Text>
                  </Pressable>
                </View>
              </View>
            </>
          )}

          {/* ══════════ STEP 3: Results ══════════ */}
          {currentStep === 3 && (
            <View style={{ gap: 12 }}>

              {/* Loading */}
              {isAnalyzing && (
                <View style={[s.card, s.centerBox]}>
                  <ActivityIndicator size="large" color="#757575" />
                  <Text style={s.loadingTitle}>分析包裝數據中...</Text>
                  <Text style={s.loadingSubtitle}>
                    邊緣節點正載入 OCR 與化學添加物名單，並同步檢驗廠商歷年違規紀錄...
                  </Text>
                </View>
              )}

              {/* Error */}
              {!isAnalyzing && analysisError && (
                <View style={[s.card, s.errorBox]}>
                  <AlertOctagon size={28} color="#dc2626" />
                  <Text style={s.errorTitle}>資訊檢驗逾時或連線失敗</Text>
                  <Text style={s.errorMsg}>{analysisError}</Text>
                  <Pressable style={s.retryBtn} onPress={handleBackToScan}>
                    <RotateCcw size={14} color="#757575" />
                    <Text style={s.retryText}>重新嘗試分析</Text>
                  </Pressable>
                </View>
              )}

              {/* Results */}
              {!isAnalyzing && !analysisError && analysisResult && (
                <>
                  {/* Top nav */}
                  <View style={[s.row, s.resultNav]}>
                    <Pressable style={s.btnSecondary} onPress={handleBackToScan}>
                      <RotateCcw size={13} color="#757575" />
                      <Text style={s.btnSecondaryText}>重新檢測</Text>
                    </Pressable>
                    <Text style={s.navTimestamp}>資料校準時間：2026</Text>
                  </View>

                  {matchedAllergens.length > 0 && (
                    <View style={s.allergenHitBanner}>
                      <View style={s.allergenHitHeader}>
                        <AlertOctagon size={20} color="#fff" />
                        <Text style={s.allergenHitTitle}>個人化過敏原警示</Text>
                      </View>
                      <Text style={s.allergenHitSub}>根據您的設定，此產品含有以下過敏原：</Text>
                      <View style={s.allergenHitBadgeRow}>
                        {matchedAllergens.map((a, i) => (
                          <View key={i} style={s.allergenHitBadge}>
                            <Text style={s.allergenHitBadgeText}>{a}</Text>
                          </View>
                        ))}
                      </View>
                    </View>
                  )}

                  {analysisResult.cached === false && (
                    <View style={s.cacheMissBanner}>
                      <Info size={13} color="#92400e" />
                      <Text style={s.cacheMissBannerText}>
                        {uploadedImages.length === 0
                          ? '首次分析此產品 · 下次可同時上傳標籤照片以提升 AI 解析準確度'
                          : '首次分析此產品 · AI 即時運算完成'}
                      </Text>
                    </View>
                  )}

                  {activeDetailView === null ? (
                    <>
                      {/* Product name compact header */}
                      {analysisResult.product_info && (
                        <View style={s.productCompact}>
                          <View style={s.productIcon}>
                            <ShoppingBag size={18} color="#757575" />
                          </View>
                          <View style={{ flex: 1 }}>
                            <Text style={s.productName} numberOfLines={1}>{analysisResult.product_info.name}</Text>
                            <Text style={s.productMeta}>
                              {analysisResult.product_info.brand} ／ {analysisResult.product_info.manufacturer}
                            </Text>
                          </View>
                          {analysisResult.product_info.barcode && (
                            <View style={s.barcodePill}>
                              <Text style={s.barcodePillText}>{analysisResult.product_info.barcode}</Text>
                            </View>
                          )}
                        </View>
                      )}

                      {/* Allergen warnings compact */}
                      {generalAllergenWarnings.length > 0 && (
                        <View style={s.card}>
                          {generalAllergenWarnings.map((w, i) => (
                            <View key={i} style={[s.allergenWarnBox, i > 0 && { marginTop: 6 }]}>
                              <AlertOctagon size={14} color="#b45309" />
                              <Text style={s.allergenWarnText}>{w}</Text>
                            </View>
                          ))}
                        </View>
                      )}

                      {/* Gauge — main dashboard chart */}
                      <Gauge
                        score={scoreValue}
                        type="health"
                        label="營養結構佔比評估 (糖分與熱量)"
                        pos={dynamicPos}
                        neg={dynamicNeg}
                        healthSegments={healthSegments}
                      />

                      {/* Personal additive risk — only shown when profile matches */}
                      {personalAdditiveRisks.length > 0 && (
                        <View style={s.personalRiskCard}>
                          <View style={s.row}>
                            <AlertTriangle size={15} color="#b45309" />
                            <Text style={s.personalRiskTitle}>個人化添加物風險警示</Text>
                          </View>
                          {personalAdditiveRisks.map((r, i) => (
                            <View key={i} style={s.personalRiskRow}>
                              <Text style={s.personalRiskName}>{r.name}</Text>
                              <Text style={s.personalRiskReason}>{r.reason}</Text>
                            </View>
                          ))}
                        </View>
                      )}

                      {/* Entry rows */}
                      <View style={s.card}>
                        {/* Breakdown */}
                        <Pressable style={s.entryRow} onPress={() => setActiveDetailView('breakdown')}>
                          <View style={s.gatewayIcon}>
                            <Grid size={16} color="#757575" />
                          </View>
                          <View style={{ flex: 1 }}>
                            <Text style={s.entryRowTitle}>成分細則扣分分解</Text>
                            <Text style={s.entryRowSub}>{scoreBreakdownList.length} 項評分項目</Text>
                          </View>
                          <ChevronRight size={15} color={TEXT_MID} />
                        </Pressable>

                        <View style={s.entryDivider} />

                        {/* Additives */}
                        <Pressable style={s.entryRow} onPress={() => setActiveDetailView('additives')}>
                          <View style={s.gatewayIcon}>
                            <Layers size={16} color="#757575" />
                          </View>
                          <View style={{ flex: 1 }}>
                            <Text style={s.entryRowTitle}>化學配料與食品添加物</Text>
                            <Text style={s.entryRowSub}>
                              {totalAdditivesCount} 種添加物
                              {highRiskCount > 0 ? `・${highRiskCount} 項高風險` : '・無高風險項目'}
                            </Text>
                          </View>
                          {highRiskCount > 0
                            ? <View style={s.entryBadgeWarn}><Text style={s.entryBadgeWarnText}>注意</Text></View>
                            : <View style={s.entryBadgeSafe}><Text style={s.entryBadgeSafeText}>安全</Text></View>
                          }
                          <ChevronRight size={15} color={TEXT_MID} />
                        </Pressable>

                        <View style={s.entryDivider} />

                        {/* History */}
                        <Pressable style={s.entryRow} onPress={() => setActiveDetailView('history')}>
                          <View style={s.gatewayIcon}>
                            <ShieldCheck size={16} color="#757575" />
                          </View>
                          <View style={{ flex: 1 }}>
                            <Text style={s.entryRowTitle}>製造廠商食安事件</Text>
                            <Text style={s.entryRowSub}>
                              {safeFoodSafetyEvents.length > 0
                                ? `發現 ${safeFoodSafetyEvents.length} 件稽查記錄`
                                : '查無重大不合格通報'}
                            </Text>
                          </View>
                          {safeFoodSafetyEvents.length > 0
                            ? <View style={s.entryBadgeWarn}><Text style={s.entryBadgeWarnText}>查看</Text></View>
                            : <View style={s.entryBadgeSafe}><Text style={s.entryBadgeSafeText}>安全</Text></View>
                          }
                          <ChevronRight size={15} color={TEXT_MID} />
                        </Pressable>
                      </View>
                    </>
                  ) : activeDetailView === 'breakdown' ? (
                    // ── Breakdown detail view ──
                    <View style={{ gap: 12 }}>
                      <Pressable style={[s.backNav, s.row]} onPress={() => setActiveDetailView(null)}>
                        <ArrowLeft size={15} color={TEXT_DARK} />
                        <Text style={s.backNavText}>返回診斷儀表板</Text>
                      </Pressable>
                      <View style={s.card}>
                        <Text style={s.cardTitle}>成分細則扣分分解</Text>
                        <View style={s.aiBox}>
                          <View style={s.row}>
                            <Sparkles size={12} color="#757575" />
                            <Text style={s.aiLabel}>Gemini AI 智慧評估綜述</Text>
                          </View>
                          <Text style={s.aiText}>
                            {analysisResult.overall_summary || getAiProductSummary(analysisResult, allIngredients, totalAdditivesCount)}
                          </Text>
                        </View>
                        <View style={{ marginTop: 12, gap: 8 }}>
                          {scoreBreakdownList.length > 0 ? (
                            scoreBreakdownList.map((item, idx) => (
                              <View key={idx} style={s.breakdownRow}>
                                <View style={{ flex: 1 }}>
                                  <Text style={s.breakdownReason}>{item.reason}</Text>
                                  <Text style={s.breakdownDesc}>{item.description}</Text>
                                </View>
                                <View style={[
                                  s.scoreBadge,
                                  item.points > 0 ? s.scoreBadgeGreen : item.points < 0 ? s.scoreBadgeRed : s.scoreBadgeGray,
                                ]}>
                                  <Text style={[
                                    s.scoreNum,
                                    item.points > 0 ? s.scoreNumGreen : item.points < 0 ? s.scoreNumRed : s.scoreNumGray,
                                  ]}>
                                    {item.points > 0 ? `+${item.points}` : item.points}分
                                  </Text>
                                </View>
                              </View>
                            ))
                          ) : (
                            <Text style={{ fontSize: 11, color: '#A89481', fontStyle: 'italic' }}>
                              無特定營養比例增扣減明細
                            </Text>
                          )}
                        </View>
                      </View>
                    </View>
                  ) : activeDetailView === 'additives' ? (
                    // ── Additives detail view ──
                    <View style={{ gap: 12 }}>
                      <Pressable style={[s.backNav, s.row]} onPress={() => setActiveDetailView(null)}>
                        <ArrowLeft size={15} color={TEXT_DARK} />
                        <Text style={s.backNavText}>返回診斷儀表板</Text>
                      </Pressable>
                      <View style={s.card}>
                        <Text style={s.cardTitle}>完整化學配料安全分級報告</Text>
                        <View style={s.aiBox}>
                          <View style={s.row}>
                            <Sparkles size={12} color="#757575" />
                            <Text style={s.aiLabel}>AI 配方添加劑快速提要</Text>
                          </View>
                          <Text style={s.aiText}>
                            {analysisResult.additives_summary || getAiAdditivesSummary(analysisResult, totalAdditivesCount, highRiskCount)}
                          </Text>
                        </View>
                        <View style={[s.row, { gap: 8, marginTop: 12, marginBottom: 4 }]}>
                          {[
                            { label: '配料總數', val: `${allIngredients.length}`, col: '#4D3A31' },
                            { label: '添加物種數', val: `${totalAdditivesCount}`, col: '#991b1b' },
                            { label: '高特定風險', val: `${highRiskCount}`, col: highRiskCount > 0 ? '#b45309' : '#009B52' },
                          ].map(st => (
                            <View key={st.label} style={[s.miniStat, { flex: 1 }]}>
                              <Text style={[s.miniStatNum, { color: st.col }]}>{st.val}</Text>
                              <Text style={s.miniStatLabel}>{st.label}</Text>
                            </View>
                          ))}
                        </View>
                        <IngredientsList ingredients={allIngredients} />
                      </View>
                      <View style={[s.tipBox, s.row, { alignItems: 'flex-start' }]}>
                        <Info size={14} color={TEXT_MID} style={{ marginTop: 2 }} />
                        <Text style={[s.tipText, { flex: 1 }]}>
                          雖然本食品中之人工添加劑均符合衛生福利部食品藥物管理署 (TFDA) 標準，但過度攝入仍可能增加身體代謝負擔，建議配合均衡飲食。
                        </Text>
                      </View>
                    </View>
                  ) : (
                    // ── History detail view ──
                    <View style={{ gap: 12 }}>
                      <Pressable style={[s.backNav, s.row]} onPress={() => setActiveDetailView(null)}>
                        <ArrowLeft size={15} color={TEXT_DARK} />
                        <Text style={s.backNavText}>返回診斷儀表板</Text>
                      </Pressable>
                      <View style={s.card}>
                        <Text style={s.cardTitle}>製造廠商歷年食安稽查詳情</Text>
                        <Text style={s.cardSubtitle}>檢索自政府開放之不合格公告名單</Text>
                        <View style={s.aiBox}>
                          <View style={s.row}>
                            <Sparkles size={12} color="#757575" />
                            <Text style={s.aiLabel}>AI 廠商稽查信用簡析</Text>
                          </View>
                          <Text style={s.aiText}>{analysisResult.safety_events_summary || getAiHistorySummary(analysisResult)}</Text>
                        </View>
                        <View style={{ marginTop: 12 }}>
                          {safeFoodSafetyEvents.length > 0 ? (
                            safeFoodSafetyEvents.map((evt, idx) => (
                              <DropdownEvent key={idx} event={evt} index={idx} />
                            ))
                          ) : (
                            <View style={s.emptyEvents}>
                              <Text style={s.emptyEventsText}>
                                目前在政府公開食品黑名單中，無此製造廠之重大安全稽處記錄。
                              </Text>
                            </View>
                          )}
                        </View>
                      </View>
                    </View>
                  )}
                </>
              )}
            </View>
          )}

          <View style={{ height: 40 }} />
        </ScrollView>
      </KeyboardAvoidingView>

      {/* Advanced settings modal */}
      <Modal
        visible={advancedVisible}
        transparent
        animationType="slide"
        onRequestClose={() => setAdvancedVisible(false)}
      >
        <Pressable style={s.modalBackdrop} onPress={() => setAdvancedVisible(false)} />
        <View style={s.modalSheet}>
          <View style={s.modalHandle} />
          <View style={s.modalHeader}>
            <Text style={s.modalTitle}>進階設定</Text>
            <Pressable onPress={() => setAdvancedVisible(false)} style={s.modalCloseBtn}>
              <X size={16} color={TEXT_MID} />
            </Pressable>
          </View>

          <Text style={s.modalSectionLabel}>食品檢核計算節點</Text>
          <Text style={s.modalSectionSub}>選擇分析請求要送往哪個後端節點</Text>
          <View style={s.segmentedControl}>
            <Pressable
              style={[s.segBtn, serverEndpoint === 'fog' && s.segBtnActive]}
              onPress={() => setServerEndpoint('fog')}
            >
              <Text style={[s.segBtnText, serverEndpoint === 'fog' && s.segBtnTextActive]}>
                微型邊緣霧節點 (Fog)
              </Text>
            </Pressable>
            <Pressable
              style={[s.segBtn, serverEndpoint === 'cloud' && s.segBtnActiveCloud]}
              onPress={() => setServerEndpoint('cloud')}
            >
              <Text style={[s.segBtnText, serverEndpoint === 'cloud' && s.segBtnTextActive]}>
                雲端伺服器 (Cloud)
              </Text>
            </Pressable>
          </View>

          <Text style={[s.modalSectionLabel, { marginTop: 20 }]}>快取管理</Text>
          <Text style={s.modalSectionSub}>清除 Fog 節點已暫存的分析結果，下次掃描將重新計算</Text>
          <Pressable
            style={[s.clearCacheBtn, clearCacheStatus === 'loading' && { opacity: 0.6 }]}
            onPress={handleClearFogCache}
            disabled={clearCacheStatus === 'loading'}
          >
            <Trash2 size={14} color={clearCacheStatus === 'success' ? '#009B52' : clearCacheStatus === 'error' ? '#ef4444' : '#757575'} />
            <Text style={[s.clearCacheBtnText, clearCacheStatus === 'success' && { color: '#009B52' }, clearCacheStatus === 'error' && { color: '#ef4444' }]}>
              {clearCacheStatus === 'loading' ? '清除中...' : clearCacheStatus === 'success' ? '清除成功' : clearCacheStatus === 'error' ? '清除失敗' : '清除 Fog 暫存'}
            </Text>
          </Pressable>

          <Pressable style={[s.btnPrimary, { marginTop: 16 }]} onPress={() => setAdvancedVisible(false)}>
            <Text style={s.btnPrimaryText}>確認</Text>
          </Pressable>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────
const BRAND = '#009B52';       // OFF green
const BRAND_LIGHT = '#E6F7EE';
const BORDER = '#DDDDDD';
const TEXT_DARK = '#1A1A1A';
const TEXT_MID = '#757575';
const GREEN = '#009B52';
const GREEN_LIGHT = '#E6F7EE';
const CTA = '#0097A7';         // OFF cyan — primary action
const CTA_DARK = '#007380';
const SEL_BG = '#E0F5F7';
const SEL_BORDER = '#5EC4CC';
const SEL_TEXT = '#007380';

const createStyles = (scale: number) => StyleSheet.create({
  root: { flex: 1, backgroundColor: '#F5F5F5' },
  scroll: { paddingHorizontal: 20, paddingVertical: 16, gap: 0 },

  // Card — no box, content directly on background
  card: {
    paddingVertical: 16,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(200,184,154,0.4)',
  },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 },
  cardTitle: { fontSize: 14 * scale, fontWeight: '800', color: TEXT_DARK },
  cardSubtitle: { fontSize: 11 * scale, color: TEXT_MID, lineHeight: 16, marginBottom: 4 },

  // Labels
  sectionLabel: { fontSize: 10 * scale, fontWeight: '800', color: TEXT_DARK, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 8 },

  // Group selector
  groupGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  groupBtn: {
    paddingHorizontal: 14, paddingVertical: 9,
    borderRadius: 12, borderWidth: 1,
    borderColor: BORDER, backgroundColor: BRAND_LIGHT,
  },
  groupBtnActive: { backgroundColor: BRAND, borderColor: BRAND },
  groupBtnText: { fontSize: 12 * scale, fontWeight: '600', color: TEXT_MID },
  groupBtnTextActive: { color: '#fff', fontWeight: '700' },

  // Allergen grid
  allergenGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 7 },
  allergenBtn: {
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 10, borderWidth: 1,
    borderColor: BORDER, backgroundColor: BRAND_LIGHT,
  },
  allergenBtnActive: { backgroundColor: '#fff1f2', borderColor: '#fecaca' },
  allergenText: { fontSize: 12 * scale, fontWeight: '600', color: TEXT_MID },
  allergenTextActive: { color: '#991b1b', fontWeight: '700' },

  // Buttons
  btnPrimary: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6,
    paddingVertical: 14, borderRadius: 16,
    backgroundColor: CTA,
  },
  btnPrimaryText: { color: '#fff', fontSize: 14 * scale, fontWeight: '700' },
  btnSecondary: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 4,
    paddingVertical: 12, paddingHorizontal: 14, borderRadius: 14,
    backgroundColor: BRAND_LIGHT, borderWidth: 1, borderColor: BORDER,
  },
  btnSecondaryText: { color: BRAND, fontSize: 13 * scale, fontWeight: '700' },

  // Row
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },

  // Segmented control
  segmentedControl: {
    flexDirection: 'row', backgroundColor: BRAND_LIGHT,
    borderRadius: 14, borderWidth: 1, borderColor: BORDER,
    padding: 4,
  },
  segBtn: { flex: 1, paddingVertical: 10, borderRadius: 10, alignItems: 'center' },
  segBtnActive: { backgroundColor: CTA },
  segBtnActiveCloud: { backgroundColor: CTA_DARK },
  segBtnText: { fontSize: 11 * scale, fontWeight: '700', color: TEXT_MID },
  segBtnTextActive: { color: '#fff' },

  // TextInput
  textInput: {
    flex: 1, backgroundColor: '#fff',
    borderWidth: 1, borderColor: BORDER,
    borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12,
    fontSize: 13 * scale, fontWeight: '600', color: TEXT_DARK,
  },
  customInlineInput: {
    backgroundColor: '#fff',
    borderWidth: 1, borderColor: BORDER,
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8,
    fontSize: 12 * scale, color: TEXT_DARK,
    marginTop: 8,
  },
  sampleBtn: {
    paddingHorizontal: 14, paddingVertical: 12,
    backgroundColor: BRAND_LIGHT, borderRadius: 12,
    borderWidth: 1, borderColor: BORDER,
  },
  sampleBtnText: { fontSize: 11 * scale, fontWeight: '700', color: BRAND },

  // Demo chips
  demoLabel: { fontSize: 10 * scale, color: TEXT_MID, fontWeight: '700' },
  demoChip: {
    paddingHorizontal: 10, paddingVertical: 5,
    backgroundColor: BRAND_LIGHT, borderRadius: 8,
    borderWidth: 1, borderColor: BORDER,
  },
  demoChipText: { fontSize: 10 * scale, color: TEXT_MID, fontWeight: '600', fontFamily: 'monospace' },

  // Results nav
  resultNav: { justifyContent: 'space-between', paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: 'rgba(200,184,154,0.4)' },
  navTimestamp: { fontSize: 10 * scale, color: TEXT_MID, fontWeight: '700' },

  // Product card
  productIcon: { padding: 10, backgroundColor: BRAND_LIGHT, borderRadius: 14, borderWidth: 1, borderColor: BORDER },
  productName: { fontSize: 15 * scale, fontWeight: '900', color: TEXT_DARK, lineHeight: 20 },
  productMeta: { fontSize: 11 * scale, color: TEXT_MID, marginTop: 4, fontWeight: '600' },
  barcodePill: {
    alignSelf: 'flex-start', marginTop: 6,
    backgroundColor: BRAND_LIGHT, borderWidth: 1, borderColor: BORDER,
    borderRadius: 8, paddingHorizontal: 8, paddingVertical: 3,
  },
  barcodePillText: { fontSize: 10 * scale, color: BRAND, fontWeight: '700', fontFamily: 'monospace' },

  // AI box
  aiBox: {
    marginTop: 12, backgroundColor: BRAND_LIGHT,
    borderRadius: 14, padding: 14, borderWidth: 1, borderColor: BORDER, gap: 6,
  },
  aiLabel: { fontSize: 10 * scale, fontWeight: '700', color: BRAND, textTransform: 'uppercase', letterSpacing: 0.4 },
  aiText: { fontSize: 12 * scale, color: TEXT_DARK, lineHeight: 18, fontWeight: '600' },

  // Allergen hit banner（頂部獨立警示）
  allergenHitBanner: {
    backgroundColor: '#dc2626', borderRadius: 16, padding: 16, gap: 10,
    shadowColor: '#dc2626', shadowOpacity: 0.35, shadowRadius: 10, shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },
  allergenHitHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  allergenHitTitle: { fontSize: 15 * scale, fontWeight: '900', color: '#fff' },
  allergenHitSub: { fontSize: 12 * scale, color: 'rgba(255,255,255,0.85)', lineHeight: 17 },
  allergenHitBadgeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  allergenHitBadge: {
    backgroundColor: 'rgba(255,255,255,0.2)', borderRadius: 10,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.5)',
    paddingHorizontal: 12, paddingVertical: 5,
  },
  allergenHitBadgeText: { fontSize: 13 * scale, fontWeight: '800', color: '#fff' },

  // Allergen warning
  allergenWarnBox: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    backgroundColor: '#fff1f2', borderRadius: 12, padding: 12,
    borderWidth: 1, borderColor: '#fecaca', marginTop: 8,
  },
  allergenWarnText: { flex: 1, fontSize: 11 * scale, color: '#7f1d1d', lineHeight: 16, fontWeight: '500' },

  // Score breakdown
  breakdownRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    backgroundColor: '#fff', borderRadius: 10, padding: 12,
    borderWidth: 1, borderColor: BORDER, gap: 12,
  },
  breakdownReason: { fontSize: 12 * scale, fontWeight: '700', color: TEXT_DARK },
  breakdownDesc: { fontSize: 10 * scale, color: TEXT_MID, marginTop: 2, lineHeight: 14 },
  scoreBadge: { borderRadius: 6, paddingHorizontal: 8, paddingVertical: 4, borderWidth: 1 },
  scoreBadgeGreen: { backgroundColor: '#E6F7EE', borderColor: '#6FCF97' },
  scoreBadgeRed: { backgroundColor: '#fff1f2', borderColor: '#fecaca' },
  scoreBadgeGray: { backgroundColor: '#f9fafb', borderColor: '#e5e7eb' },
  scoreNum: { fontSize: 11 * scale, fontWeight: '900' },
  scoreNumGreen: { color: '#009B52' },
  scoreNumRed: { color: '#991b1b' },
  scoreNumGray: { color: '#6b7280' },

  // Gateway cards
  gatewayCard: {
    paddingVertical: 16,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(200,184,154,0.4)',
    gap: 12,
  },
  gatewayHeader: { gap: 8 },
  gatewayIcon: { padding: 8, backgroundColor: BRAND_LIGHT, borderRadius: 12 },
  enterBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 2,
    backgroundColor: BRAND_LIGHT, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 20,
    alignSelf: 'flex-start',
    borderWidth: 1, borderColor: BORDER,
  },
  enterBadgeText: { fontSize: 11 * scale, fontWeight: '700', color: BRAND },

  // Stat boxes
  statBox: {
    padding: 12, borderRadius: 12, borderWidth: 1,
    borderColor: BORDER, backgroundColor: '#fff', alignItems: 'center',
  },
  statBoxRed: { backgroundColor: '#fff1f2', borderColor: '#fecaca' },
  statBoxGreen: { backgroundColor: '#E6F7EE', borderColor: '#6FCF97' },
  statNum: { fontSize: 20 * scale, fontWeight: '900', color: TEXT_DARK },
  statLabel: { fontSize: 9 * scale, fontWeight: '700', color: TEXT_MID, textTransform: 'uppercase' },

  // Event status
  eventTeaser: {
    backgroundColor: '#fffbeb', borderRadius: 10, padding: 10,
    borderWidth: 1, borderColor: '#fde68a',
  },
  eventTeaserText: { fontSize: 12 * scale, fontWeight: '700', color: '#92400e' },
  eventSafe: {
    backgroundColor: GREEN_LIGHT, borderRadius: 10, padding: 10,
    borderWidth: 1, borderColor: 'rgba(45,106,79,0.3)',
  },
  eventSafeText: { fontSize: 12 * scale, fontWeight: '700', color: GREEN },

  // Loading / error
  centerBox: { alignItems: 'center', gap: 12, paddingVertical: 40 },
  loadingTitle: { fontSize: 14 * scale, fontWeight: '700', color: TEXT_DARK },
  loadingSubtitle: { fontSize: 12 * scale, color: TEXT_MID, textAlign: 'center', lineHeight: 18, maxWidth: 280 },
  errorBox: { alignItems: 'center', gap: 10, paddingVertical: 30, backgroundColor: '#fff1f2', borderColor: '#fecaca' },
  errorTitle: { fontSize: 14 * scale, fontWeight: '900', color: '#7f1d1d' },
  errorMsg: { fontSize: 12 * scale, color: '#991b1b', textAlign: 'center', lineHeight: 17 },
  retryBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    marginTop: 4, paddingVertical: 10, paddingHorizontal: 16,
    backgroundColor: BRAND_LIGHT, borderRadius: 12, borderWidth: 1, borderColor: BORDER,
  },
  retryText: { fontSize: 12 * scale, fontWeight: '700', color: BRAND },

  // Detail views
  backNav: {
    backgroundColor: BRAND_LIGHT, borderRadius: 14, paddingVertical: 12, paddingHorizontal: 16,
    borderWidth: 1, borderColor: BORDER,
  },
  backNavText: { fontSize: 13 * scale, fontWeight: '700', color: BRAND },
  miniStat: { backgroundColor: '#fff', borderRadius: 12, padding: 10, alignItems: 'center', borderWidth: 1, borderColor: BORDER },
  miniStatNum: { fontSize: 20 * scale, fontWeight: '900' },
  miniStatLabel: { fontSize: 9 * scale, color: TEXT_MID, fontWeight: '700', textTransform: 'uppercase', textAlign: 'center' },
  emptyEvents: {
    padding: 20, borderRadius: 14, borderWidth: 1.5, borderStyle: 'dashed',
    borderColor: '#94A3B8', backgroundColor: '#fff', alignItems: 'center',
  },
  emptyEventsText: { fontSize: 11 * scale, color: TEXT_MID, textAlign: 'center', lineHeight: 17 },

  tipBox: {
    padding: 14, borderRadius: 14, borderWidth: 1,
    borderColor: '#fde68a', backgroundColor: 'rgba(255,251,235,0.6)',
  },
  tipText: { fontSize: 11 * scale, color: TEXT_MID, lineHeight: 17 },

  chevron: { fontSize: 11 * scale, color: TEXT_MID },

  // Input mode toggle (Step 2)
  inputModeRow: { flexDirection: 'row', gap: 8, marginTop: 8 },
  inputModeBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7,
    paddingVertical: 11, borderRadius: 14,
    backgroundColor: BRAND_LIGHT, borderWidth: 1, borderColor: BORDER,
  },
  inputModeBtnActive: { backgroundColor: SEL_BG, borderColor: SEL_BORDER },
  inputModeBtnText: { fontSize: 13 * scale, fontWeight: '700', color: BRAND },
  inputModeBtnTextActive: { color: SEL_TEXT },

  // Scan launch button
  scanLaunchBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 14,
    backgroundColor: BRAND_LIGHT, borderRadius: 16, padding: 16,
    borderWidth: 1, borderColor: BORDER,
  },
  scanLaunchIcon: {
    width: 46, height: 46, borderRadius: 14,
    backgroundColor: '#fff', borderWidth: 1, borderColor: BORDER,
    alignItems: 'center', justifyContent: 'center',
  },
  scanLaunchTitle: { fontSize: 14 * scale, fontWeight: '800', color: TEXT_DARK },
  scanLaunchSub: { fontSize: 11 * scale, color: TEXT_MID, marginTop: 2, lineHeight: 15 },

  // Scanned result badge
  scannedResult: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: GREEN_LIGHT, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8,
    borderWidth: 1, borderColor: 'rgba(45,106,79,0.25)',
  },
  scannedResultText: { flex: 1, fontSize: 12 * scale, fontWeight: '600', color: GREEN, fontFamily: 'monospace' },
  scannedClear: { fontSize: 11 * scale, fontWeight: '700', color: BRAND },

  // Matched allergens
  matchedAllergenRow: {
    flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap',
    gap: 6, marginBottom: 8,
    backgroundColor: '#fff1f2', borderRadius: 10,
    paddingHorizontal: 10, paddingVertical: 8,
    borderWidth: 1, borderColor: '#fecaca',
  },
  matchedAllergenLabel: { fontSize: 11 * scale, fontWeight: '800', color: '#991b1b' },
  matchedAllergenBadge: {
    backgroundColor: '#dc2626', borderRadius: 8,
    paddingHorizontal: 8, paddingVertical: 3,
  },
  matchedAllergenBadgeText: { fontSize: 11 * scale, fontWeight: '700', color: '#fff' },

  // Cache miss banner
  cacheMissBanner: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    backgroundColor: '#fffbeb', borderRadius: 12, padding: 12,
    borderWidth: 1, borderColor: '#fde68a',
  },
  cacheMissBannerText: { flex: 1, fontSize: 12 * scale, color: '#92400e', lineHeight: 17, fontWeight: '600' },

  // Font scale toggle (header "Aa" button)
  fontScaleBtn: {
    position: 'absolute', right: 16, bottom: 32, zIndex: 99,
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 20, borderWidth: 1, borderColor: BORDER,
    backgroundColor: '#fff',
    shadowColor: BRAND, shadowOpacity: 0.15, shadowRadius: 8, shadowOffset: { width: 0, height: 2 },
    elevation: 4,
  },
  fontScaleBtnActive: { backgroundColor: BRAND, borderColor: BRAND },
  fontScaleBtnText: { fontSize: 12 * scale, fontWeight: '700', color: BRAND },
  fontScaleBtnTextActive: { color: '#fff' },

  // Personal additive risk card
  personalRiskCard: {
    backgroundColor: '#fffbeb', borderRadius: 16, padding: 16,
    borderWidth: 1, borderColor: '#fde68a', gap: 10,
  },
  personalRiskTitle: { fontSize: 13 * scale, fontWeight: '800', color: '#92400e', flex: 1 },
  personalRiskRow: {
    backgroundColor: '#fff', borderRadius: 10, padding: 10,
    borderWidth: 1, borderColor: '#fde68a', gap: 3,
  },
  personalRiskName: { fontSize: 12 * scale, fontWeight: '700', color: '#78350f' },
  personalRiskReason: { fontSize: 11 * scale, color: '#92400e', lineHeight: 16 },

  // Product compact header
  productCompact: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingVertical: 14,
    borderBottomWidth: 1, borderBottomColor: 'rgba(200,184,154,0.4)',
  },

  // Entry rows (dashboard)
  entryRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingVertical: 14,
  },
  entryDivider: { height: 1, backgroundColor: BORDER },
  entryRowTitle: { fontSize: 13 * scale, fontWeight: '700', color: TEXT_DARK },
  entryRowSub: { fontSize: 11 * scale, color: TEXT_MID, marginTop: 2 },
  entryBadgeSafe: {
    backgroundColor: SEL_BG, borderRadius: 8, borderWidth: 1, borderColor: SEL_BORDER,
    paddingHorizontal: 8, paddingVertical: 3,
  },
  entryBadgeSafeText: { fontSize: 10 * scale, fontWeight: '700', color: SEL_TEXT },
  entryBadgeWarn: {
    backgroundColor: '#fffbeb', borderRadius: 8, borderWidth: 1, borderColor: '#fde68a',
    paddingHorizontal: 8, paddingVertical: 3,
  },
  entryBadgeWarnText: { fontSize: 10 * scale, fontWeight: '700', color: '#92400e' },

  // Advanced settings entry button
  advancedBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    marginTop: 16, paddingVertical: 10, paddingHorizontal: 14,
    borderRadius: 12, borderWidth: 1, borderColor: BORDER,
    backgroundColor: '#fff',
  },
  advancedBtnText: { flex: 1, fontSize: 12 * scale, fontWeight: '600', color: TEXT_MID },
  advancedEndpointBadge: {
    backgroundColor: SEL_BG, borderRadius: 8, borderWidth: 1, borderColor: SEL_BORDER,
    paddingHorizontal: 8, paddingVertical: 3,
  },
  advancedEndpointBadgeText: { fontSize: 10 * scale, fontWeight: '700', color: SEL_TEXT },

  // Modal
  modalBackdrop: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.35)',
  },
  modalSheet: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 24, borderTopRightRadius: 24,
    padding: 24, paddingBottom: 40,
    borderTopWidth: 1, borderColor: BORDER,
  },
  modalHandle: {
    width: 40, height: 4, borderRadius: 2,
    backgroundColor: BORDER, alignSelf: 'center', marginBottom: 16,
  },
  modalHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20,
  },
  modalTitle: { fontSize: 16 * scale, fontWeight: '900', color: TEXT_DARK },
  modalCloseBtn: {
    padding: 6, borderRadius: 10, backgroundColor: BRAND_LIGHT,
    borderWidth: 1, borderColor: BORDER,
  },
  modalSectionLabel: { fontSize: 12 * scale, fontWeight: '800', color: TEXT_DARK, marginBottom: 4 },
  modalSectionSub: { fontSize: 11 * scale, color: TEXT_MID, marginBottom: 12 },
  clearCacheBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 10, paddingHorizontal: 14,
    borderRadius: 10, borderWidth: 1, borderColor: BORDER,
    backgroundColor: '#F5F5F5',
  },
  clearCacheBtnText: { fontSize: 13 * scale, fontWeight: '700', color: TEXT_MID },
});
