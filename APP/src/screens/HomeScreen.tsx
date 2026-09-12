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
import { Sparkles, Sliders, Database, ShoppingBag, AlertOctagon, RotateCcw, Layers, ShieldCheck, Grid, ChevronRight, Zap, ChevronUp, ChevronDown, ArrowLeft, AlertTriangle, CheckCircle2, Info, ScanLine, Activity, Settings2, X } from 'lucide-react-native';

import { useFontScale } from '../contexts/FontScaleContext';
import { UserConditions, AnalysisResponse } from '../types';
import { MOCK_RESULTS } from '../mockResultData';
import Gauge from '../components/Gauge';
import IngredientsList from '../components/IngredientsList';
import DropdownEvent from '../components/DropdownEvent';
import PackageImageScanner from '../components/PackageImageScanner';
import BarcodeScanner from '../components/BarcodeScanner';
import { analyzePersonalRisks, getProductAllergenWarnings } from '../utils/personalization';
import { FOG_URL, CLOUD_URL, ANALYSIS_TIMEOUT_MS } from '../constants/endpoints';
import { getDataFreshness } from '../utils/dataFreshness';
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

  // 掃描是進入 App 的第一個畫面；健康設定改由右下角齒輪進入（2026-08-05）
  const [view, setView] = useState<'scan' | 'profile' | 'result'>('scan');
  const [targetGroup, setTargetGroup] = useState<UserConditions['group']>('adult');
  const [allergens, setAllergens] = useState<string[]>([]);
  const [customAllergen, setCustomAllergen] = useState('');
  const [chronicDiseases, setChronicDiseases] = useState<string[]>([]);

  // 註：群體與慢性病的「自定義」文字欄位已於 2026-08-05 移除。它們輸入的是中文自由
  // 文字，但比對對象 groupRisks[].group 只會是 7 個英文碼、慢性病閾值也只認
  // hypertension/diabetes，中文字串兩邊都對不上，打了不會觸發任何東西。
  // 過敏原的自定義欄位保留——它會直接以輸入文字比對成分與標示，法定 11 大類以外
  // 的過敏原（如木瓜）確實需要它才能警示。
  useEffect(() => {
    AsyncStorage.multiGet(['targetGroup', 'customAllergen', 'chronicDiseases', 'allergens']).then(pairs => {
      pairs.forEach(([key, value]) => {
        if (!value) return;
        if (key === 'targetGroup') setTargetGroup(value as UserConditions['group']);
        if (key === 'customAllergen') setCustomAllergen(value);
        if (key === 'chronicDiseases') setChronicDiseases(JSON.parse(value));
        if (key === 'allergens') setAllergens(JSON.parse(value));
      });
    });
  }, []);

  const saveTargetGroup = (value: UserConditions['group']) => {
    setTargetGroup(value);
    AsyncStorage.setItem('targetGroup', value);
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
  const [barcodeInput, setBarcodeInput] = useState('');
  const [uploadedImages, setUploadedImages] = useState<string[]>([]);
  const [serverEndpoint, setServerEndpoint] = useState<'fog' | 'cloud'>('fog');

  const [advancedVisible, setAdvancedVisible] = useState(false);
  // 其他成分預設收合：使用者要看的是添加物，其餘配料是備查用的
  const [othersExpanded, setOthersExpanded] = useState(false);
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
    setView('result');
  };


  const handleSubmitAnalysis = async () => {
    if (!barcodeInput && uploadedImages.length === 0) return;

    // Fast-path for known mock barcodes
    if (uploadedImages.length === 0 && MOCK_RESULTS[barcodeInput]) {
      setAnalysisResult(MOCK_RESULTS[barcodeInput]);
      setAnalysisError(null);
      setView('result');
      return;
    }

    setIsAnalyzing(true);
    setAnalysisResult(null);
    setAnalysisError(null);
    setView('result');

    try {
      const API_URL = serverEndpoint === 'fog' ? FOG_URL : CLOUD_URL;
      // 逾時：在這之前完全沒設，靠平台預設（各家不同），網路斷掉時畫面會一直轉。
      // 120 秒比下游每一層都長，好讓後端寫好的錯誤訊息與降階結果送得到使用者
      // 眼前，而不是被 App 自己先掐掉。數字的推導見 constants/endpoints.ts。
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), ANALYSIS_TIMEOUT_MS);
      let response: Response;
      try {
        response = await fetch(API_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-cache' },
          signal: ctrl.signal,
          // 個人化比對自 2026-08-04 起完全在本地進行，健康背景不再送往後端。
          body: JSON.stringify({
            barcode: barcodeInput,
            label_images: uploadedImages,
          }),
        });
      } finally {
        // 成功時也要清掉，否則這個 timer 會一直活到 120 秒後才觸發 abort()，
        // 對已完成的請求沒作用但會讓測試環境留著未回收的計時器。
        clearTimeout(timer);
      }
      if (!response.ok) throw new Error(`伺服器代碼: ${response.status}`);
      const json = await response.json();
      // Fog 可能將結果包在 data 欄位內
      const result = json.health_score !== undefined ? json : (json.data ?? json);

      // 「分析失敗」在兩層都是以 HTTP 200 ＋ {status, message} 回傳，不是 HTTP 錯誤：
      //   Cloud — rejected（照片沒通過品質閘門）／not_found（查無條碼）／error
      //   Fog   — degraded（Cloud 不可用且無快取）
      // 這些形狀都沒有 health_score。若只看 response.ok 就會把它們當成有效結果，
      // 結果頁的 `health_score ?? 100` 會顯示一個假的 100 分，而後端寫好的引導訊息
      // （「請對準成分表重新拍攝」等）永遠不會被看到。
      if (result?.health_score === undefined) {
        throw new Error(
          result?.message ?? json?.message ?? '無法完成分析，請確認條碼或照片後再試一次',
        );
      }

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
      // AbortError 要單獨講：「連不上」與「等太久」對使用者是不同的下一步，
      // 前者去檢查網路或伺服器，後者重試或少拍幾張就好。
      const msg = err?.name === 'AbortError'
        ? `分析逾時（超過 ${ANALYSIS_TIMEOUT_MS / 1000} 秒）。請確認網路後重試，或減少照片張數。`
        : err?.message ?? '無法連線至後端分析節點，請確認伺服器有正常運作！';
      setAnalysisError(msg);
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleBackToScan = () => {
    setView('scan');
    setAnalysisResult(null);
    setAnalysisError(null);
    setActiveDetailView(null);
    setIsScoreExpanded(false);
  };

  // ── Derived stats ──────────────────────────────────────────────────────────
  const allIngredients = analysisResult?.ingredients_detail ?? [];
  const safeFoodSafetyEvents = analysisResult?.food_safety_events ?? [];

  /**
   * 是否顯示「製造廠商食安事件」。
   *
   * Cloud 端的 SAFETY_EVENTS_ENABLED 目前為 false（server/main.py），該處註解寫明
   * 「整條路徑（背景蒐集與前端呈現）一併停用」，但前端其實一直還顯示著，而且是以
   * 綠色「安全」徽章 ＋「查無重大不合格通報」呈現——那是在宣稱查過了沒問題，
   * 實際上系統根本沒查。對食安 App 而言把「未查詢」呈現成「安全」是有風險的，
   * 故 2026-08-05 起改為只在真的有事件時才顯示，補上原本沒停到的那一半。
   *
   * 功能恢復時要注意：Cloud 目前無法區分「未查詢」與「查過但無事」，兩者都回空
   * 陣列（見 module_d/response_builder.py 的 safety_events_summary 分支）。若要讓
   * 「查過無事」也顯示為正面資訊，需先讓 Cloud 明確回報這兩種狀態的差別。
   */
  const hasSafetyEvents = safeFoodSafetyEvents.length > 0;

  /**
   * 添加物與其他成分分開呈現。
   *
   * 使用者真正要看的是「添加物是什麼、有什麼作用」，其餘配料（水、糖、麵粉）
   * 不需要解釋。原本兩者混在同一份清單、只用徽章區分，25 項裡有 5 項要緊的
   * 資訊就被稀釋掉。
   *
   * ⚠ **但那條分界線本身是會錯的**，而錯的代價不對稱：
   *   把真添加物歸進「其他成分」＝主動告訴使用者「這不是添加物」
   *   把非添加物列進「添加物」＝多解釋一項，使用者自己看得出來
   * 2026-09-10 以 177 案量測（`PPOCR_TEST/實驗設計/16`）：
   *   分欄新增的錯誤是 30／521 ＝ 5.8%，集中在長化學名的比對失敗
   * 因此「其他成分」的標題**必須寫成「未比對到添加物資料庫」而不是
   * 「非添加物」**——同一份資料、同樣的錯誤率，但一個是誠實的缺口、
   * 一個是錯誤陳述。
   */
  const isAdditive = (i: typeof allIngredients[number]) =>
    i.isAdditive === true || i.isAdditive === 'true';
  const additiveIngredients = allIngredients.filter(isAdditive);
  const otherIngredients = allIngredients.filter(i => !isAdditive(i));
  const totalAdditivesCount = additiveIngredients.length;
  const highRiskCount = allIngredients.filter(i => {
    if (!(i.isAdditive === true || i.isAdditive === 'true')) return false;
    return (i.groupRisks ?? []).some(r => r.riskLevel >= 3);
  }).length;

  const scoreBreakdownList = getScoreBreakdownList(analysisResult);

  // 資料新鮮度：讓使用者知道手上這份分析是何時算出來的，以及是否為降級回應
  const dataFreshness = getDataFreshness(analysisResult);

  // ── 本地個人化比對（健康背景不離開裝置）──────────────────────────────────
  // 通用警告＝產品標示本身；舊快取中 Fog 寫入的「偵測到過敏原：X」屬於前一位
  // 使用者的比對結果，一律濾除不顯示。
  const generalAllergenWarnings = getProductAllergenWarnings(analysisResult);

  const {
    matchedAllergens,
    additiveRisks: personalAdditiveRisks,
    nutritionWarnings,
  } = analyzePersonalRisks(analysisResult, {
    group: targetGroup,
    allergens: [...allergens, ...(customAllergen.trim() ? [customAllergen.trim()] : [])],
    chronicConditions: chronicDiseases,
  });

  // 已設定的健康條件數量，顯示在齒輪上——否則設定被收起來後，使用者無從得知
  // 自己到底有沒有設過、設了什麼。
  const profileConfiguredCount =
    (targetGroup !== 'adult' ? 1 : 0) +
    chronicDiseases.length +
    allergens.length +
    (customAllergen.trim() ? 1 : 0);

  const { pos: dynamicPos, neg: dynamicNeg } = getDynamicHealthPoints(analysisResult, scoreBreakdownList);
  const healthSegments = getDynamicHealthSegments(analysisResult, scoreBreakdownList);
  const healthLegend = getDynamicHealthLegend(scoreBreakdownList);
  const scoreValue = analysisResult?.health_score ?? 100;

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={s.root}>
      <Pressable onPress={toggleFontScale} style={[s.fontScaleBtn, isLarge && s.fontScaleBtnActive]}>
        <Text style={[s.fontScaleBtnText, isLarge && s.fontScaleBtnTextActive]}>Aa</Text>
      </Pressable>

      {/* 健康設定入口。設定畫面本身不顯示，避免與畫面內的返回鍵重複。 */}
      {view !== 'profile' && (
        <Pressable
          onPress={() => setView('profile')}
          style={s.profileGearBtn}
          accessibilityLabel="個人健康設定"
          accessibilityRole="button"
        >
          <Sliders size={16} color={BRAND} />
          {profileConfiguredCount > 0 && (
            <View style={s.profileGearBadge}>
              <Text style={s.profileGearBadgeText}>{profileConfiguredCount}</Text>
            </View>
          )}
        </Pressable>
      )}
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView contentContainerStyle={s.scroll} keyboardShouldPersistTaps="handled">

          {/* ══════════ 健康設定（由右下角齒輪進入）══════════ */}
          {view === 'profile' && (
            <View style={s.card}>
              <View style={s.profileTopBar}>
                <Pressable style={s.btnSecondary} onPress={() => setView('scan')}>
                  <ArrowLeft size={14} color="#757575" />
                  <Text style={s.btnSecondaryText}>返回掃描</Text>
                </Pressable>
              </View>
              <View style={s.cardHeader}>
                <Sliders size={18} color="#757575" />
                <Text style={s.cardTitle}>設定個人健康特徵</Text>
              </View>
              <Text style={s.cardSubtitle}>建立受檢者的健康資料，系統將依此提供個人化風險警告。設定會自動儲存。</Text>

              {/* Group selector */}
              <Text style={s.sectionLabel}>1. 群體</Text>
              <View style={s.groupGrid}>
                {GROUPS.map(g => (
                  <Pressable
                    key={g.value}
                    onPress={() => saveTargetGroup(g.value)}
                    style={[s.groupBtn, targetGroup === g.value && s.groupBtnActive]}
                  >
                    <Text style={[s.groupBtnText, targetGroup === g.value && s.groupBtnTextActive]}>
                      {g.label}
                    </Text>
                  </Pressable>
                ))}
              </View>

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
                <Pressable style={[s.btnPrimary, { flex: 1 }]} onPress={() => setView('scan')}>
                  <CheckCircle2 size={14} color="#fff" />
                  <Text style={s.btnPrimaryText}>完成，回到掃描</Text>
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
          {view === 'scan' && (
            <>
              {/* 不傳 onPhotosSubmit：標示照片統一由下方的 PackageImageScanner 負責，
                  避免同一件事有兩個入口。掃描器因此只做條碼。 */}
              <BarcodeScanner
                visible={scannerVisible}
                onClose={() => setScannerVisible(false)}
                onBarcodeScanned={code => {
                  setBarcodeInput(code);
                  setScannerVisible(false);
                }}
              />

              <View style={s.card}>
                <View style={s.cardHeader}>
                  <Database size={18} color="#757575" />
                  <Text style={s.cardTitle}>掃描食品</Text>
                </View>
                <Text style={s.cardSubtitle}>掃條碼或拍標示照片，擇一即可；兩者都給可提升解析準確度</Text>

                {/* 條碼：輸入框 ＋ 掃描鈕（原本的手動／掃描切換已移除） */}
                <View style={[s.row, { marginTop: 16, gap: 8 }]}>
                  <TextInput
                    value={barcodeInput}
                    onChangeText={setBarcodeInput}
                    placeholder="輸入條碼，例如 4710018123456"
                    placeholderTextColor="#94A3B8"
                    style={[s.textInput, { flex: 1 }]}
                    keyboardType="numeric"
                  />
                  <Pressable
                    style={s.scanIconBtn}
                    onPress={() => setScannerVisible(true)}
                    accessibilityLabel="開啟條碼掃描器"
                    accessibilityRole="button"
                  >
                    <ScanLine size={20} color={BRAND} />
                  </Pressable>
                </View>

                {barcodeInput !== '' && (
                  <View style={[s.scannedResult, { marginTop: 8 }]}>
                    <CheckCircle2 size={13} color={GREEN} />
                    <Text style={s.scannedResultText}>條碼：{barcodeInput}</Text>
                    <Pressable onPress={() => setBarcodeInput('')}>
                      <Text style={s.scannedClear}>清除</Text>
                    </Pressable>
                  </View>
                )}

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

                {/* 或 */}
                <View style={s.orDivider}>
                  <View style={s.orLine} />
                  <Text style={s.orText}>或</Text>
                  <View style={s.orLine} />
                </View>

                {/* 標示照片（唯一入口） */}
                <PackageImageScanner
                  onImageCaptured={base64 => setUploadedImages(prev => [...prev, base64])}
                  images={uploadedImages}
                  onRemoveImage={i => setUploadedImages(prev => prev.filter((_, idx) => idx !== i))}
                />

                <View style={[s.row, { marginTop: 20 }]}>
                  <Pressable
                    style={[s.btnPrimary, { flex: 1 }]}
                    onPress={handleSubmitAnalysis}
                    disabled={!barcodeInput && uploadedImages.length === 0}
                  >
                    <Text style={s.btnPrimaryText}>開始分析</Text>
                  </Pressable>
                </View>

                {/* 進階設定：開發／展示用（選後端節點、清快取），故置於主要動作之後 */}
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
              </View>
            </>
          )}

          {/* ══════════ STEP 3: Results ══════════ */}
          {view === 'result' && (
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
                  {/* 這個框同時服務「連線失敗」與「後端判定無法分析」兩種情況，
                      標題保持中性，實際原因由後端給的 message 說明。 */}
                  <Text style={s.errorTitle}>無法完成分析</Text>
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
                    {/* 原本這裡寫死「資料校準時間：2026」，對使用者沒有任何資訊。
                        改為顯示 Cloud 實際計算此分析的時間（快取命中時即為原始計算時間）。 */}
                    <Text style={[s.navTimestamp, dataFreshness.isStale && s.navTimestampStale]}>
                      {dataFreshness.label}
                    </Text>
                  </View>

                  {/* Cloud 不可用、Fog 以過期快取降級回應時，必須讓使用者知道 */}
                  {dataFreshness.warning && (
                    <View style={s.staleBanner}>
                      <Info size={13} color="#b45309" />
                      <Text style={s.staleBannerText}>{dataFreshness.warning}</Text>
                    </View>
                  )}

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

                      {/* Chronic-condition nutrition thresholds — sodium / sugar */}
                      {nutritionWarnings.length > 0 && (
                        <View style={s.personalRiskCard}>
                          <View style={s.row}>
                            <Activity size={15} color="#b45309" />
                            <Text style={s.personalRiskTitle}>個人化營養警示</Text>
                          </View>
                          {nutritionWarnings.map((w, i) => (
                            <View key={i} style={s.personalRiskRow}>
                              <Text style={s.personalRiskName}>{w.condition}族群{w.title}</Text>
                              <Text style={s.personalRiskReason}>{w.detail}</Text>
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

                        {/* History — 僅在真的有事件時顯示，見 hasSafetyEvents 的說明 */}
                        {hasSafetyEvents && (
                          <>
                            <View style={s.entryDivider} />
                            <Pressable style={s.entryRow} onPress={() => setActiveDetailView('history')}>
                              <View style={s.gatewayIcon}>
                                <ShieldCheck size={16} color="#757575" />
                              </View>
                              <View style={{ flex: 1 }}>
                                <Text style={s.entryRowTitle}>製造廠商食安事件</Text>
                                <Text style={s.entryRowSub}>
                                  發現 {safeFoodSafetyEvents.length} 件稽查記錄
                                </Text>
                              </View>
                              <View style={s.entryBadgeWarn}><Text style={s.entryBadgeWarnText}>查看</Text></View>
                              <ChevronRight size={15} color={TEXT_MID} />
                            </Pressable>
                          </>
                        )}
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
                        {additiveIngredients.length > 0 ? (
                          <IngredientsList ingredients={additiveIngredients} />
                        ) : (
                          /* ⚠ 空清單**不等於**「本產品不含添加物」。
                           *
                           * 2026-09-10 以 177 案量測：添加物為 0 的案子有 42 個，
                           * 其中 11 個是「標示上有、我們沒讀到」。而系統**分不出來**
                           * ——兩組的成分項數分布幾乎完全重疊（危險組中位 10 項、
                           * 乾淨組中位 9 項），任何門檻都是抓到少數、誤傷一堆
                           * （成分項數 <10 只抓到 5/11，卻誤傷 19/31）。
                           *
                           * 既然分不出來，畫面就不能宣稱任何一邊。這與 2026-08-05
                           * 把食安事件從「未查詢卻顯示為安全」改掉是同一條原則：
                           * 對食安 App 而言，把「沒查到」呈現成「沒問題」有風險。 */
                          <View style={[s.tipBox, s.row, { alignItems: 'flex-start' }]}>
                            <AlertTriangle size={14} color="#b45309" style={{ marginTop: 2 }} />
                            <View style={{ flex: 1, gap: 4 }}>
                              <Text style={[s.tipText, { fontWeight: '800', color: TEXT_DARK }]}>
                                未偵測到添加物
                              </Text>
                              <Text style={s.tipText}>
                                這可能是產品確實未使用，也可能是成分標示沒有辨識成功。
                                系統無法分辨這兩種情況，
                                <Text style={{ fontWeight: '800' }}>請以包裝上的成分欄為準</Text>。
                              </Text>
                            </View>
                          </View>
                        )}

                        {otherIngredients.length > 0 && (
                          <View style={{ marginTop: 12 }}>
                            <Pressable
                              onPress={() => setOthersExpanded(v => !v)}
                              style={[s.row, { justifyContent: 'space-between', paddingVertical: 6 }]}
                            >
                              {/* ⚠ 標題不可寫成「非添加物」。這一欄是「沒有比對到
                               *    添加物資料庫」，不是「確定不是添加物」——實測
                               *    有 5.8% 的真添加物會落在這裡（見上方 derived 區註解）。*/}
                              <Text style={[s.cardTitle, { fontSize: 13 * fontScale }]}>
                                其他成分（{otherIngredients.length}）
                                <Text style={s.cardSubtitle}>　未比對到添加物資料庫</Text>
                              </Text>
                              {othersExpanded
                                ? <ChevronUp size={14} color={TEXT_MID} />
                                : <ChevronDown size={14} color={TEXT_MID} />}
                            </Pressable>
                            {othersExpanded && <IngredientsList ingredients={otherIngredients} />}
                          </View>
                        )}
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
                    // 目前進不來：唯一的入口列已隨 hasSafetyEvents 隱藏（Cloud 端功能停用中）。
                    // 刻意保留整段而非刪除——功能恢復時只要 Cloud 開始回傳事件，入口列會自動
                    // 出現，這裡不必重寫。下方的空狀態文案僅在「有入口但事件為空」時才會被看到，
                    // 那是功能恢復後才可能出現的情境。
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

          {/* 「快取管理」區塊已於 2026-08-05 移除。它呼叫 POST :3001/cache/clear，
              但 Fog 的 Node 層（3001）只註冊了 DELETE /cache 與 DELETE /cache/:barcode，
              該路由不存在 → 一直是 404，按下去只會顯示「清除失敗」。
              POST /cache/clear 只存在於 Python 層（3002），而 App 連不到 3002。
              Fog 的快取機制本身不受影響，仍照常運作（TTL 到期自動失效）。
              若之後要恢復：在 fog/server.ts 補一條轉發路由，並補上路由存在性測試。 */}

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
  navTimestampStale: { color: '#b45309' },

  // Cloud 不可用、以過期快取降級回應時的提示橫幅
  staleBanner: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: '#fffbeb', borderRadius: 10, padding: 10,
    borderWidth: 1, borderColor: '#fcd34d',
  },
  staleBannerText: { flex: 1, fontSize: 11 * scale, color: '#92400e', lineHeight: 16 },

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

  // Scan launch button

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

  // 健康設定入口（浮動於 Aa 按鈕上方，共用同一套視覺）
  profileGearBtn: {
    position: 'absolute', right: 16, bottom: 84, zIndex: 99,
    width: 40, height: 40, borderRadius: 20,
    alignItems: 'center', justifyContent: 'center',
    borderWidth: 1, borderColor: BORDER, backgroundColor: '#fff',
    shadowColor: BRAND, shadowOpacity: 0.15, shadowRadius: 8, shadowOffset: { width: 0, height: 2 },
    elevation: 4,
  },
  profileGearBadge: {
    position: 'absolute', top: -4, right: -4,
    minWidth: 18, height: 18, borderRadius: 9, paddingHorizontal: 4,
    alignItems: 'center', justifyContent: 'center',
    backgroundColor: BRAND, borderWidth: 2, borderColor: '#fff',
  },
  profileGearBadgeText: { fontSize: 10, fontWeight: '700', color: '#fff' },

  // 健康設定頁最上方的返回列
  profileTopBar: { flexDirection: 'row', marginBottom: 12 },

  // 條碼輸入框旁的掃描鈕（取代原本的手動／掃描切換）
  scanIconBtn: {
    width: 48, height: 48, borderRadius: 12,
    alignItems: 'center', justifyContent: 'center',
    borderWidth: 1, borderColor: BORDER, backgroundColor: '#fff',
  },

  // 「或」分隔線
  orDivider: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 20, marginBottom: 4 },
  orLine: { flex: 1, height: 1, backgroundColor: BORDER },
  orText: { fontSize: 12 * scale, color: TEXT_MID, fontWeight: '600' },

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
});
