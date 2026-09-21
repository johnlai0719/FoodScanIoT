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
  Share,
} from 'react-native';
import { Sparkles, Sliders, Database, ShoppingBag, AlertOctagon, RotateCcw, Layers, ShieldCheck, ChevronRight, Zap, ArrowLeft, AlertTriangle, CheckCircle2, Info, ScanLine, Activity, Settings2 } from 'lucide-react-native';

import { useFontScale } from '../contexts/FontScaleContext';
import { UserConditions, AnalysisResponse, DegradedLocalResponse } from '../types';
import { MOCK_RESULTS } from '../mockResultData';
import Gauge from '../components/Gauge';
import IngredientsList from '../components/IngredientsList';
import DropdownEvent from '../components/DropdownEvent';
import PackageImageScanner from '../components/PackageImageScanner';
import BarcodeScanner from '../components/BarcodeScanner';
import { analyzePersonalRisks, getProductAllergenWarnings } from '../utils/personalization';
import { FOG_URL, CLOUD_URL, CLOUD_API_KEY, ANALYSIS_TIMEOUT_MS } from '../constants/endpoints';
import * as Telemetry from '../utils/telemetry';
import { getDataFreshness } from '../utils/dataFreshness';
import {
  getScoreBreakdownList,
  getDynamicHealthPoints,
  getDynamicHealthSegments,
  getBonusSegments,
  getDynamicHealthLegend,
} from '../utils/scoring';
import {
  getAiProductSummary,
  getAiAdditivesSummary,
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
  // 三高。label 用「高血糖」而不是「糖尿病」：這裡量的是營養素閾值，
  // 不是在診斷疾病（key 仍是 diabetes，沿用既有的儲存值與比對代碼）。
  { key: 'hypertension', label: '高血壓' },
  { key: 'diabetes', label: '高血糖' },
  { key: 'hyperlipidemia', label: '高血脂' },
];


// ─── Component ────────────────────────────────────────────────────────────────
export default function HomeScreen() {
  const { fontScale, toggleFontScale, isLarge } = useFontScale();
  const s = createStyles(fontScale);

  // 掃描是進入 App 的第一個畫面；健康設定改由右下角齒輪進入（2026-08-05）
  const [view, setView] = useState<'scan' | 'profile' | 'result' | 'settings'>('scan');
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

  // 其他成分預設收合：使用者要看的是添加物，其餘配料是備查用的
  const [scannerVisible, setScannerVisible] = useState(false);

  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisResult, setAnalysisResult] = useState<AnalysisResponse | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  // Fog 在 Cloud 連不上且無快取時，用本機 OCR 回的部分結果。
  // **與 analysisResult 分開存**：它刻意沒有 health_score，混在一起會讓
  // 結果頁的 `health_score ?? 100` 顯示一個不存在的分數
  // （`normalize_result` 憑空補 75 分那次就是這樣來的）。
  const [degradedResult, setDegradedResult] = useState<DegradedLocalResponse | null>(null);
  // 測試模式：送 X-Bypass-Cache，讓 Fog 跳過**讀**快取（照常寫）。
  // 量延遲時每一次都要走完整路徑，命中快取的那幾次會把中位數拉到失真。
  const [bypassCache, setBypassCache] = useState(false);
  // 只用本機辨識：送 X-Local-Only，Fog 直接走本機 OCR，照片不轉送雲端。
  // 與「雲端掛了才降階」走同一條產出路徑，但**是使用者選的**——結果頁要
  // 分得出是哪一種，否則使用者會以為系統出問題了。
  const [localOnly, setLocalOnly] = useState(false);
  // 這一次的結果是不是在本機模式下拿到的。不可直接用 localOnly 判斷——
  // 使用者可能在看結果時又去把開關關掉，那樣標示就與實際產出不符。
  const [resultWasLocalOnly, setResultWasLocalOnly] = useState(false);
  const [activeDetailView, setActiveDetailView] = useState<'additives' | 'history' | 'breakdown' | null>(null);
  // 添加物明細底下再分三頁。null＝三個統計卡的總覽頁。
  // 分頁的理由：成分與添加物同列一頁時，使用者要捲很久才看得完，
  // 而且兩份清單長得一樣，捲到一半就分不清現在看的是哪一份。
  const [additiveSubView, setAdditiveSubView] = useState<'all' | 'additives' | 'risky' | null>(null);
  // 這一次分析花了多久。**手機自己量的**（同一個時鐘兩次相減），
  // 不是把各層的時間戳相減——三層分處不同機器，時鐘不保證同步。
  // 這個數字包含網路往返，是使用者實際等待的時間。
  const [lastTiming, setLastTiming] = useState<
    { totalMs: number; cloudMs: number | null; cached: boolean } | null>(null);
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
    setDegradedResult(null);
    setView('result');

    // ── 實驗埋點 ────────────────────────────────────────────────────────
    // request id 逐層轉發，讓 App／Fog／Cloud 三份紀錄併得起來。
    // t0 只與 t1 相減（同一個時鐘），不與任何伺服器時刻比對——決策單 #15
    // 未定的正是跨機器時鐘校正，這裡整個繞過。
    // ⚠ 宣告在 try 之外：**失敗與降階那幾次才是最需要量的**，
    //   放在 try 裡面 finally 就取不到，那些次會剛好沒有紀錄。
    const reqId = Telemetry.newRequestId();
    const payloadChars = uploadedImages.reduce((n, b) => n + b.length, 0);
    const t0 = Date.now();
    let httpStatus: number | null = null;
    let respHeaders: Headers | null = null;
    let recResult: any = null;
    let recError: string | null = null;

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
          headers: {
            'Content-Type': 'application/json',
            'Cache-Control': 'no-cache',
            'X-Request-Id': reqId,
            // 專用標頭而不是沿用上面那個 Cache-Control——它每次都送，
            // 拿它當判準等於永久關閉快取，就量不出快取有沒有幫上忙。
            ...(bypassCache ? { 'X-Bypass-Cache': '1' } : {}),
            // 只用本機辨識。Fog 端若本機 OCR 未就緒會回 503 而**不會**
            // 改送雲端——那正是選這個選項要避免的事。
            ...(localOnly ? { 'X-Local-Only': '1' } : {}),
            // Cloud 直連時帶上金鑰，以通過 Cloud 的 API 保護。
            ...(serverEndpoint === 'cloud' && CLOUD_API_KEY ? { 'X-API-Key': CLOUD_API_KEY } : {}),
          },
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
      httpStatus = response.status;
      respHeaders = response.headers;
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
      // Fog 的本機降階：**有部分結果，不是失敗。**
      // 它刻意沒有 health_score（見 fog/transforms.build_degraded_local_response），
      // 所以不能跟下面那個「沒有分數就是失敗」的判斷混在一起。
      // degraded_mode 是它與「Cloud 診斷降級」的區別——後者仍有分數。
      if (result?.status === 'degraded' && result?.degraded_mode === 'local_ocr') {
        recResult = result;
        setDegradedResult(result as DegradedLocalResponse);
        return;
      }

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
        'overall_summary', 'additives_summary',
      ];
      const missing = EXPECTED_FIELDS.filter(f => result[f] == null);
      const nulled  = EXPECTED_FIELDS.filter(f => f in result && result[f] === null);
      if (missing.length > 0) console.warn('[Fog] 缺少欄位:', missing.join(', '));
      if (nulled.length > 0)  console.warn('[Fog] null 欄位:', nulled.join(', '));
      console.log('[Fog] 收到欄位:', Object.keys(result).join(', '));
      console.log('[Fog] cached:', result.cached, '| score:', result.health_score, '| ingredients:', result.ingredients_detail?.length ?? 'null');
      console.log('[Fog] score_breakdown:', JSON.stringify(result.score_breakdown?.slice(0, 5)));
      // ────────────────────────────────────────────────────────────────────

      recResult = result;
      setAnalysisResult(result);
    } catch (err: any) {
      // AbortError 要單獨講：「連不上」與「等太久」對使用者是不同的下一步，
      // 前者去檢查網路或伺服器，後者重試或少拍幾張就好。
      const msg = err?.name === 'AbortError'
        ? `分析逾時（超過 ${ANALYSIS_TIMEOUT_MS / 1000} 秒）。請確認網路後重試，或減少照片張數。`
        : err?.message ?? '無法連線至後端分析節點，請確認伺服器有正常運作！';
      recError = msg;
      setAnalysisError(msg);
    } finally {
      setIsAnalyzing(false);
      setResultWasLocalOnly(localOnly);
      {
        // Cloud 自報的分析耗時（X-Timing-Cloud 的 total）。與手機量到的差額
        // 就是網路與各層轉發——兩個都顯示，使用者才看得出慢在哪一段。
        const cloud = Telemetry.parseTiming(respHeaders?.get('X-Timing-Cloud') ?? null);
        setLastTiming({
          totalMs: Date.now() - t0,
          cloudMs: cloud?.total ?? null,
          // 快取命中時耗時會低一個量級，不標出來會讓人以為辨識變快了。
          cached: (respHeaders?.get('X-Cache') ?? '').toLowerCase().includes('hit'),
        });
      }
      // 一次掃描一列。**刻意不記照片與成分原文**——一是隱私（紀錄會被匯出），
      // 二是能拿來算指標的是「有幾項」而不是內容。
      void Telemetry.append({
        request_id: reqId,
        at: new Date().toISOString(),
        endpoint: serverEndpoint,
        barcode: barcodeInput,
        n_images: uploadedImages.length,
        payload_chars: payloadChars,
        http_status: httpStatus,
        total_ms: Date.now() - t0,
        fog_node: Telemetry.parseTiming(respHeaders?.get('X-Timing-Fog-Node') ?? null),
        fog_py: Telemetry.parseTiming(respHeaders?.get('X-Timing-Fog-Py') ?? null),
        cloud: Telemetry.parseTiming(respHeaders?.get('X-Timing-Cloud') ?? null),
        cache: respHeaders?.get('X-Cache') ?? null,
        bypass_cache: bypassCache,
        status: recResult?.status ?? null,
        health_score: recResult?.health_score ?? null,
        risk_level: recResult?.risk_level ?? null,
        n_additives: recResult?.ingredients_detail?.length ?? null,
        // 降階與失敗要分開統計。degraded_mode 是 Fog 本機 OCR 那條路
        // （transforms.build_degraded_local_response），與 Cloud 的診斷降級不同。
        degraded: recResult?.status === 'degraded' || !!recResult?.degraded_mode,
        error: recError,
      });
    }
  };

  // ── 實驗量測紀錄的匯出／清除 ───────────────────────────────────────────
  const [telemetryCount, setTelemetryCount] = useState(0);
  useEffect(() => {
    // 每次進到進階設定才重讀：掃描完不即時更新筆數，省一次 AsyncStorage 讀取，
    // 而使用者要看筆數時本來就得開這一頁。
    if (view === 'settings') void Telemetry.load().then(r => setTelemetryCount(r.length));
  }, [view]);

  const handleExportTelemetry = async () => {
    const records = await Telemetry.load();
    if (records.length === 0) {
      setAnalysisError('目前沒有量測紀錄。先掃幾張照片再匯出。');
      return;
    }
    const sum = Telemetry.summarize(records);
    try {
      await Share.share({
        title: `FoodScan 量測 ${records.length} 筆`,
        // 摘要放在最前面，手機上不必捲到底就看得到輪廓。
        message: `# FoodScan 量測紀錄
# ${JSON.stringify(sum)}

`
          + Telemetry.toCSV(records),
      });
    } catch {
      // 使用者取消分享也會走到這裡，不是錯誤，不要顯示訊息。
    }
  };

  const handleClearTelemetry = async () => {
    await Telemetry.clear();
    setTelemetryCount(0);
  };

  const handleBackToScan = () => {
    setView('scan');
    setAnalysisResult(null);
    setAnalysisError(null);
    setDegradedResult(null);
    setActiveDetailView(null);
    setAdditiveSubView(null);
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
   * 陣列。若要讓
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
  const totalAdditivesCount = additiveIngredients.length;
  // 「須注意」＝有族群風險等級 ≥3 的添加物。只算添加物，不含未比對到的成分——
  // 未比對到就沒有族群風險資料，混進來會讓這個數字看起來比實際能判定的多。
  const riskyIngredients = allIngredients.filter(i => {
    if (!(i.isAdditive === true || i.isAdditive === 'true')) return false;
    return (i.groupRisks ?? []).some(r => r.riskLevel >= 3);
  });
  const highRiskCount = riskyIngredients.length;

  // 依官方類別統計添加物。一項可能屬多類，故各類次數相加會大於添加物總數——
  // 這是對的，不可為了「加起來等於總數」而只取第一類：那會讓「己二烯酸鉀」
  // 這種同時是防腐劑與殺菌劑的項目憑空少掉一個身分。畫面上要講明這件事。
  const additiveCategoryStats = (() => {
    const m = new Map<string, number>();
    let uncategorised = 0;
    for (const i of additiveIngredients) {
      const cats = i.category ?? [];
      if (cats.length === 0) { uncategorised += 1; continue; }
      for (const c of cats) m.set(c, (m.get(c) ?? 0) + 1);
    }
    const rows = [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    // 「未分類」永遠排最後，且與其他類別分開命名——它不是一種類別，
    // 是「這項沒配到知識庫」，混在中間會被讀成官方分類之一。
    if (uncategorised > 0) rows.push(['未分類', uncategorised]);
    return rows;
  })();

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

  // ── 降階結果的本地比對 ──────────────────────────────────────────────────
  // 過敏原偵測與族群添加物風險**只吃 ingredients_detail**，降階資料就跑得起來
  // （checkNutritionThresholds 需要營養值，降階沒有，它會安全地回空陣列）。
  // 這一塊是安全關鍵：Cloud 掛掉時最該保留的正是過敏原示警。
  const degradedRisks = analyzePersonalRisks(
    // 只帶 ingredients_detail：刻意不偽造成完整的 AnalysisResponse，
    // 免得日後有人把它當成真的分析結果往下傳。
    degradedResult ? ({ ingredients_detail: degradedResult.ingredients_detail } as AnalysisResponse) : null,
    {
      group: targetGroup,
      allergens: [...allergens, ...(customAllergen.trim() ? [customAllergen.trim()] : [])],
      chronicConditions: chronicDiseases,
    },
  );
  const degradedAdditives = (degradedResult?.ingredients_detail ?? []).filter(isAdditive);
  const degradedOthers = (degradedResult?.ingredients_detail ?? []).filter(i => !isAdditive(i));

  // 已設定的健康條件數量，顯示在齒輪上——否則設定被收起來後，使用者無從得知
  // 自己到底有沒有設過、設了什麼。
  const profileConfiguredCount =
    (targetGroup !== 'adult' ? 1 : 0) +
    chronicDiseases.length +
    allergens.length +
    (customAllergen.trim() ? 1 : 0);

  const { pos: dynamicPos, neg: dynamicNeg } = getDynamicHealthPoints(analysisResult, scoreBreakdownList);
  const healthSegments = getDynamicHealthSegments(analysisResult, scoreBreakdownList);
  const bonusSegments = getBonusSegments(scoreBreakdownList);
  const healthLegend = getDynamicHealthLegend(scoreBreakdownList);
  const scoreValue = analysisResult?.health_score ?? 100;

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={s.root}>
      <Pressable onPress={toggleFontScale} style={[s.fontScaleBtn, isLarge && s.fontScaleBtnActive]}>
        <Text style={[s.fontScaleBtnText, isLarge && s.fontScaleBtnTextActive]}>Aa</Text>
      </Pressable>

      {/* 健康設定入口。設定畫面本身不顯示，避免與畫面內的返回鍵重複。 */}
      {view !== 'profile' && view !== 'settings' && (
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

          {/* ══════════ 進階設定（獨立頁面）══════════
              2026-09-13 從底部彈出的 Modal 改成獨立頁。理由：這一頁的內容
              已經不只是「切個節點」——多了實驗量測紀錄與逐段延遲，彈窗高度
              放不下、又會蓋住底下的結果。與個人健康設定同一種形狀（頂部返回列
              ＋ 一般捲動），使用者只要學一次。 */}
          {view === 'settings' && (
            <View>
              <View style={s.profileTopBar}>
                <Pressable style={s.btnSecondary} onPress={() => setView('scan')}>
                  <ArrowLeft size={13} color={BRAND} />
                  <Text style={s.btnSecondaryText}>返回掃描</Text>
                </Pressable>
              </View>

              <View style={s.card}>
                <View style={s.cardHeader}>
                  <Settings2 size={18} color={TEXT_MID} />
                  <Text style={s.cardTitle}>進階設定</Text>
                </View>
                <Text style={s.cardSubtitle}>
                  後端節點與實驗量測。這一頁是開發與展示用的，一般使用不需要動。
                </Text>
              </View>

              <Text style={s.settingsLabel}>食品檢核計算節點</Text>
              <Text style={s.settingsSub}>
                {serverEndpoint === 'cloud'
                  ? '目前為【雲端直連模式】：繞過 Fog 邊緣節點，直接由雲端進行完整分析（對照實驗用）。'
                  : '目前為【微型邊緣霧節點模式】：透過 Fog 進行快取、資料脫敏與邊緣降階（主要推薦路徑）。'}
              </Text>
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
                  onPress={() => {
                    setServerEndpoint('cloud');
                    if (localOnly) setLocalOnly(false);
                  }}
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

              {/* ── 實驗量測紀錄 ────────────────────────────────────────────────
                  一次掃描一列，存在裝置本地（AsyncStorage），由使用者自己匯出。
                  **不自動回傳任何東西**——個人化資料不上雲是本專案明文的界線，
                  量測紀錄同樣比照。匯出走 RN 內建的 Share，不額外加依賴。 */}
              <Text style={[s.settingsLabel, { marginTop: 24 }]}>測試模式</Text>
              <Text style={s.settingsSub}>
                不使用 Fog 快取，每次都走完整路徑（App → Fog → Cloud → 辨識）。
                量延遲時要開；一般使用請關閉，否則每次都要重新辨識。
              </Text>
              <View style={s.segmentedControl}>
                <Pressable
                  style={[s.segBtn, !bypassCache && s.segBtnActive]}
                  onPress={() => setBypassCache(false)}
                >
                  <Text style={[s.segBtnText, !bypassCache && s.segBtnTextActive]}>
                    使用快取
                  </Text>
                </Pressable>
                <Pressable
                  style={[s.segBtn, bypassCache && s.segBtnActiveCloud]}
                  onPress={() => setBypassCache(true)}
                >
                  <Text style={[s.segBtnText, bypassCache && s.segBtnTextActive]}>
                    每次重新辨識
                  </Text>
                </Pressable>
              </View>

              <Text style={[s.settingsLabel, { marginTop: 24 }]}>只用本機辨識（降階用 FOG）</Text>
              <Text style={s.settingsSub}>
                開啟後啟用 Fog 邊緣降階模式，照片只送到微型邊緣節點（Fog），不再轉送雲端。回傳的是部分結果：
                有成分與過敏原，但**沒有健康評分與添加物比對**——那兩項需要雲端的
                知識庫與計分模組。邊緣節點的本機辨識若尚未就緒，會直接回報錯誤而
                不會改送雲端。
              </Text>
              <View style={s.segmentedControl}>
                <Pressable
                  style={[s.segBtn, !localOnly && s.segBtnActive]}
                  onPress={() => setLocalOnly(false)}
                >
                  <Text style={[s.segBtnText, !localOnly && s.segBtnTextActive]}>
                    完整分析 (Cloud)
                  </Text>
                </Pressable>
                <Pressable
                  style={[s.segBtn, localOnly && s.segBtnActiveCloud]}
                  onPress={() => {
                    setLocalOnly(true);
                    setServerEndpoint('fog');
                  }}
                >
                  <Text style={[s.segBtnText, localOnly && s.segBtnTextActive]}>
                    啟用降階用 FOG
                  </Text>
                </Pressable>
              </View>

              <Text style={[s.settingsLabel, { marginTop: 20 }]}>實驗量測紀錄</Text>
              <Text style={s.settingsSub}>
                已累積 {telemetryCount} 筆，存在本機。匯出為 CSV 後可直接做統計。
              </Text>
              <View style={s.segmentedControl}>
                <Pressable style={s.segBtn} onPress={handleExportTelemetry}>
                  <Text style={s.segBtnText}>匯出 CSV</Text>
                </Pressable>
                <Pressable style={s.segBtn} onPress={handleClearTelemetry}>
                  <Text style={s.segBtnText}>清除紀錄</Text>
                </Pressable>
              </View>

              <Pressable style={[s.btnPrimary, { marginTop: 24 }]} onPress={() => setView('scan')}>
                <Text style={s.btnPrimaryText}>完成</Text>
              </Pressable>
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
                <Pressable style={s.advancedBtn} onPress={() => setView('settings')}>
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

              {/* ── 離線降階（Fog 本機 OCR）────────────────────────────────
                  刻意做成第三種狀態，不是「成功」也不是「失敗」：
                  - **絕不顯示分數。** 這份回應沒有 health_score，硬填一個就是
                    編造（`normalize_result` 補 75 分那次的教訓）。
                  - **明確列出拿不到什麼**，而不是安靜省略。理由同
                    `NOT_IN_DB_LABEL` 那條：「沒有資料」不可呈現成「沒有問題」。
                  - 過敏原與族群風險照樣比對——那是安全關鍵，且只需要成分清單。 */}
              {!isAnalyzing && !analysisError && degradedResult && (
                <>
                  <View style={[s.row, s.resultNav]}>
                    <Pressable style={s.btnSecondary} onPress={handleBackToScan}>
                      <RotateCcw size={13} color="#757575" />
                      <Text style={s.btnSecondaryText}>重新檢測</Text>
                    </Pressable>
                    {/* 降階時也要顯示耗時——它常常是「等了很久才降階」，
                        而使用者只看到「離線模式」不會知道等了多久。 */}
                    {lastTiming && (
                      <Text style={s.navTiming}>
                        耗時 {(lastTiming.totalMs / 1000).toFixed(1)} 秒
                      </Text>
                    )}
                  </View>

                  <View style={[s.card, s.degradedBox]}>
                    <View style={s.row}>
                      <AlertTriangle size={20} color={DEGRADED_FG} />
                      <Text style={s.degradedTitle}>
                        {resultWasLocalOnly ? '本機辨識・部分結果' : '離線模式・部分結果'}
                      </Text>
                    </View>
                    {/* 使用者自己選的模式，不該用故障的語氣講。Fog 回的 message
                        是為「雲端掛了」寫的，這裡覆蓋掉。 */}
                    <Text style={s.degradedMsg}>
                      {resultWasLocalOnly
                        ? '依你的設定，這次只用邊緣節點辨識，照片沒有送往雲端。因此沒有健康評分與添加物比對，成分與過敏原仍以包裝標示為準。'
                        : degradedResult.message}
                    </Text>
                    <Text style={s.degradedNote}>
                      本機辨識：{degradedResult.engine?.ocr ?? '—'}・
                      {degradedResult.elapsed_s} 秒
                    </Text>
                  </View>

                  {/* 過敏原：Cloud 掛掉時最該保留的一塊 */}
                  {degradedRisks.matchedAllergens.length > 0 && (
                    <View style={[s.card, s.errorBox]}>
                      <AlertOctagon size={22} color="#dc2626" />
                      <Text style={s.errorTitle}>符合你設定的過敏原</Text>
                      <Text style={s.errorMsg}>
                        {degradedRisks.matchedAllergens.join('、')}
                      </Text>
                      {/* 誠實的但書：降階用的是純 OCR，漏讀的機率比雲端高。
                          「沒偵測到」在這個模式下不等於「不含」。 */}
                      <Text style={s.degradedNote}>
                        離線辨識可能漏讀，未列出的項目不代表不含。
                      </Text>
                    </View>
                  )}

                  {degradedRisks.additiveRisks.length > 0 && (
                    <View style={s.card}>
                      <Text style={s.cardTitle}>與你的健康條件相關</Text>
                      {degradedRisks.additiveRisks.map((r, i) => (
                        <Text key={`${r.name}-${i}`} style={s.degradedMsg}>
                          {r.name}：{r.reason}
                        </Text>
                      ))}
                    </View>
                  )}

                  {/* 添加物清單。教授的定位是「使用者主要看添加物攤開後的解釋」，
                      所以降階模式也以這一塊為主體。 */}
                  <View style={s.card}>
                    <Text style={s.cardTitle}>
                      添加物（{degradedAdditives.length}）
                    </Text>
                    {degradedAdditives.length > 0 ? (
                      <IngredientsList ingredients={degradedAdditives} />
                    ) : (
                      <Text style={s.degradedMsg}>
                        本次未比對到添加物。離線辨識可能漏讀，不代表本產品不含添加物。
                      </Text>
                    )}
                  </View>

                  {degradedOthers.length > 0 && (
                    <View style={s.card}>
                      {/* 標題必須是「未比對到添加物資料庫」而不是「非添加物」——
                          同一份資料、同樣 5.8% 的比對錯誤率，但一個是誠實的缺口、
                          一個是錯誤陳述（見上方 isAdditive 的註解）。 */}
                      <Text style={s.cardTitle}>
                        其他成分・未比對到添加物資料庫（{degradedOthers.length}）
                      </Text>
                      <IngredientsList ingredients={degradedOthers} />
                    </View>
                  )}

                  {/* 拿不到什麼，明講 */}
                  <View style={s.card}>
                    <Text style={s.cardTitle}>離線模式無法提供</Text>
                    <Text style={s.degradedMsg}>
                      {degradedResult.unavailable_fields
                        .map(f => UNAVAILABLE_LABELS[f] ?? f)
                        // 同一個中文標籤可能對應多個欄位（score_breakdown 與
                        // health_score 都是「健康評分」），去重後才不會重複列。
                        .filter((v, i, a) => a.indexOf(v) === i)
                        .join('、')}
                    </Text>
                    <Text style={s.degradedNote}>
                      這些需要雲端才能計算。恢復連線後重新檢測即可取得完整分析。
                    </Text>
                  </View>
                </>
              )}

              {/* Results */}
              {!isAnalyzing && !analysisError && !degradedResult && analysisResult && (
                <>
                  {/* Top nav */}
                  <View style={[s.row, s.resultNav]}>
                    <Pressable style={s.btnSecondary} onPress={handleBackToScan}>
                      <RotateCcw size={13} color="#757575" />
                      <Text style={s.btnSecondaryText}>重新檢測</Text>
                    </Pressable>
                    {/* 原本這裡寫死「資料校準時間：2026」，對使用者沒有任何資訊。
                        改為顯示 Cloud 實際計算此分析的時間（快取命中時即為原始計算時間）。 */}
                    <View style={{ alignItems: 'flex-end' }}>
                      <Text style={[s.navTimestamp, dataFreshness.isStale && s.navTimestampStale]}>
                        {dataFreshness.label}
                      </Text>
                      {lastTiming && (
                        <Text style={s.navTiming}>
                          {lastTiming.cached ? '快取命中・' : ''}
                          耗時 {(lastTiming.totalMs / 1000).toFixed(1)} 秒
                          {lastTiming.cloudMs != null
                            ? `（雲端 ${(lastTiming.cloudMs / 1000).toFixed(1)} 秒）`
                            : ''}
                        </Text>
                      )}
                    </View>
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
                      {/* 點分數 → 看完整算式。教授要的「占比與計算方式」在那一頁。
                          等級以 Cloud 的 nutri_grade 為準：score 是 Nutri-Score
                          原始分數（越低越好），呈現端自己推會判錯（見 Gauge.tsx）。 */}
                      <Pressable onPress={() => setActiveDetailView('breakdown')}>
                        <Gauge
                          score={scoreValue}
                          grade={analysisResult.nutri_grade}
                          type="health"
                          label="營養結構佔比評估 (糖分與熱量)"
                          pos={dynamicPos}
                          neg={dynamicNeg}
                          healthSegments={healthSegments}
                          bonusSegments={bonusSegments}
                        />
                        <View style={s.scoreTapRow}>
                          <Text style={s.scoreTapText}>
                            Nutri-Score {scoreValue} 分・分數越低越好
                          </Text>
                          <View style={s.row}>
                            <Text style={s.scoreTapLink}>看計算方式</Text>
                            <ChevronRight size={13} color={BRAND} />
                          </View>
                        </View>
                      </Pressable>

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
                              {/* 出處與複核狀態。這句話是查來的還是模型整理的，
                                  使用者有權分辨——與「輸出的每個字都要有來源」
                                  同一條原則。沒記出處時整行不顯示，不要寫成
                                  「來源：無」，那會讀起來像「查證過但沒有來源」。 */}
                              {(r.sourceTitle || r.sourceUrl || r.reviewedByHuman === false) && (
                                <Text style={s.personalRiskSource}>
                                  {r.sourceTitle || r.sourceUrl}
                                  {r.sourceYear ? `（${r.sourceYear}）` : ''}
                                  {r.reviewedByHuman === false ? '・未經人工複核' : ''}
                                </Text>
                              )}
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

                      {/* Entry rows

                          ~~評分明細~~ **2026-09-13 移除這一列**：它與分數卡下方的
                          「看逐項計算」通到同一頁，兩個入口做同一件事會讓人以為是
                          兩種不同的東西。留分數卡那個——它就貼在被解釋的對象旁邊，
                          而這個清單裝的是**其他主題**（添加物、廠商歷史），把計算
                          明細混進來反而暗示它是另一個主題。
                          （順帶一提原本的標題「成分細則扣分分解」也是錯的：
                          扣分的是營養素，不是成分。） */}
                      <View style={s.card}>
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
                        <Text style={s.cardTitle}>Nutri-Score 計算明細</Text>
                        {/* 先給總帳，再給逐項。只有逐項的話讀者得自己加，
                            而且加起來對不對也無從確認。 */}
                        <View style={s.totalsRow}>
                          <Text style={s.totalsText}>
                            扣分合計 {scoreBreakdownList.filter(i => i.points < 0)
                              .reduce((n, i) => n + Math.abs(i.points), 0)}
                            　加分合計 {scoreBreakdownList.filter(i => i.points > 0)
                              .reduce((n, i) => n + i.points, 0)}
                          </Text>
                          <Text style={s.totalsScore}>
                            = {scoreValue} 分・{analysisResult.nutri_grade ?? '—'} 級
                          </Text>
                        </View>
                        <Text style={s.breakdownDesc}>
                          分數越低越好。等級的分界隨品類而異——飲料要 2 分以下才是 B，
                          只有純水可能拿到 A。
                        </Text>
                        <View style={s.aiBox}>
                          <View style={s.row}>
                            <Info size={12} color="#757575" />
                            <Text style={s.aiLabel}>評分說明</Text>
                          </View>
                          <Text style={s.aiText}>
                            {analysisResult.overall_summary || getAiProductSummary(analysisResult, allIngredients, totalAdditivesCount)}
                          </Text>
                        </View>
                        <View style={{ marginTop: 12, gap: 8 }}>
                          {scoreBreakdownList.length > 0 ? (
                            scoreBreakdownList.map((item, idx) => {
                              // 占比＝這一項拿到的分 ÷ 這一項的上限。
                              // maxPoints 缺席時（舊快取）不畫條，也不假造一個分母。
                              const abs = Math.abs(item.points);
                              const max = item.maxPoints ?? null;
                              const frac = max ? Math.min(abs / max, 1) : null;
                              return (
                                <View key={idx} style={s.breakdownRow}>
                                  <View style={{ flex: 1 }}>
                                    <View style={s.breakdownHead}>
                                      <Text style={s.breakdownReason}>{item.reason}</Text>
                                      {/* 實測值。**null 是「未標示」不是 0**——
                                          寫成 0 會宣稱這項含量為零，那是編造。 */}
                                      <Text style={s.breakdownValue}>
                                        {item.value === null || item.value === undefined
                                          ? '未標示'
                                          : `${item.value} ${item.unit ?? ''}`.trim()}
                                      </Text>
                                    </View>
                                    {frac !== null && (
                                      <View style={s.barTrack}>
                                        <View style={[
                                          s.barFill,
                                          { width: `${frac * 100}%` },
                                          item.points > 0 ? s.barFillGreen : s.barFillRed,
                                        ]} />
                                      </View>
                                    )}
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
                                      {item.points > 0 ? `+${item.points}` : item.points}
                                    </Text>
                                    {/* 分母要寫出來：只有「−7 分」看不出是滿分還是一半。 */}
                                    {max ? <Text style={s.scoreMax}>／{max}</Text> : null}
                                  </View>
                                </View>
                              );
                            })
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
                      <Pressable
                        style={[s.backNav, s.row]}
                        onPress={() => { setActiveDetailView(null); setAdditiveSubView(null); }}
                      >
                        <ArrowLeft size={15} color={TEXT_DARK} />
                        <Text style={s.backNavText}>返回診斷儀表板</Text>
                      </Pressable>
                      {additiveSubView === null ? (
                        // ── 總覽：三個統計卡，點進去才看清單 ──
                        <View style={s.card}>
                          <Text style={s.cardTitle}>完整化學配料安全分級報告</Text>
                          <View style={s.aiBox}>
                            <View style={s.row}>
                              <Info size={12} color="#757575" />
                              <Text style={s.aiLabel}>添加物概要</Text>
                            </View>
                            <Text style={s.aiText}>
                              {analysisResult.additives_summary || getAiAdditivesSummary(analysisResult, totalAdditivesCount, highRiskCount)}
                            </Text>
                          </View>
                          <View style={[s.row, { gap: 8, marginTop: 12 }]}>
                            {([
                              { key: 'all' as const, label: '成分', val: allIngredients.length, col: '#4D3A31' },
                              { key: 'additives' as const, label: '添加物', val: totalAdditivesCount, col: '#991b1b' },
                              { key: 'risky' as const, label: '須注意', val: highRiskCount, col: highRiskCount > 0 ? '#b45309' : '#009B52' },
                            ]).map(st => (
                              <Pressable
                                key={st.key}
                                onPress={() => setAdditiveSubView(st.key)}
                                style={[s.miniStat, s.miniStatTappable, { flex: 1 }]}
                              >
                                <Text style={[s.miniStatNum, { color: st.col }]}>{st.val}</Text>
                                <Text style={s.miniStatLabel}>{st.label}</Text>
                                {/* 數字本身看不出可以點。這一列是唯一的入口，
                                    沒有它使用者會以為清單被拿掉了。 */}
                                <View style={[s.row, { gap: 2, marginTop: 4 }]}>
                                  <Text style={s.miniStatHint}>查看</Text>
                                  <ChevronRight size={10} color={TEXT_MID} />
                                </View>
                              </Pressable>
                            ))}
                          </View>
                          {totalAdditivesCount === 0 && (
                            /* ⚠ 空清單**不等於**「本產品不含添加物」。這段留在總覽頁，
                             * 因為添加物為 0 時使用者不會去點那張卡，看不到頁內的說明。
                             *
                             * 2026-09-10 以 177 案量測：添加物為 0 的案子有 42 個，
                             * 其中 11 個是「標示上有、我們沒讀到」，而系統**分不出來**
                             * ——兩組的成分項數分布幾乎完全重疊。既然分不出來，
                             * 畫面就不能宣稱任何一邊。 */
                            <View style={[s.tipBox, s.row, { alignItems: 'flex-start', marginTop: 12 }]}>
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
                        </View>
                      ) : (
                        // ── 子頁：一次只顯示一份清單 ──
                        (() => {
                          const view = {
                            all: {
                              title: '全部成分',
                              note: '依標示順序列出。標有「添加物」徽章者已比對到添加物資料庫；未標記者為未比對到，不代表確定不是添加物。',
                              list: allIngredients,
                              empty: '沒有辨識到任何成分。',
                            },
                            additives: {
                              title: '食品添加物',
                              note: '已比對到添加物資料庫的項目。',
                              list: additiveIngredients,
                              empty: '未偵測到添加物。這可能是產品確實未使用，也可能是標示沒有辨識成功——系統無法分辨，請以包裝上的成分欄為準。',
                            },
                            risky: {
                              title: '須注意的添加物',
                              note: '對特定族群（孕婦、幼童、腎功能不全者等）有明確風險提示的添加物。每一則提示都附有來源。',
                              list: riskyIngredients,
                              empty: '本產品的添加物中，沒有對特定族群的高風險提示。',
                            },
                          }[additiveSubView];
                          return (
                            <View style={s.card}>
                              {/* 返回總覽，不是直接回儀表板——少一階會讓使用者
                                  要看第二份清單時得從頭再點一次。 */}
                              <Pressable
                                style={[s.row, { paddingVertical: 4, marginBottom: 8 }]}
                                onPress={() => setAdditiveSubView(null)}
                              >
                                <ArrowLeft size={14} color={TEXT_DARK} />
                                <Text style={s.backNavText}>返回添加物總覽</Text>
                              </Pressable>
                              <Text style={s.cardTitle}>
                                {view.title}
                                <Text style={s.cardSubtitle}>　{view.list.length} 項</Text>
                              </Text>
                              <Text style={[s.tipText, { marginTop: 4, marginBottom: 8 }]}>{view.note}</Text>
                              {additiveSubView === 'additives' && additiveCategoryStats.length > 0 && (
                                <View style={s.catBox}>
                                  <Text style={s.catBoxTitle}>依類別</Text>
                                  <View style={s.catWrap}>
                                    {additiveCategoryStats.map(([cat, n]) => (
                                      <View
                                        key={cat}
                                        style={[s.catChip, cat === '未分類' && s.catChipMuted]}
                                      >
                                        <Text style={[s.catChipText, cat === '未分類' && s.catChipTextMuted]}>
                                          {cat}
                                        </Text>
                                        <Text style={[s.catChipNum, cat === '未分類' && s.catChipTextMuted]}>
                                          {n}
                                        </Text>
                                      </View>
                                    ))}
                                  </View>
                                  {/* 不解釋的話會被當成加總錯誤。 */}
                                  <Text style={s.catNote}>
                                    一項添加物可能同時屬多個類別，各類數字相加會大於添加物總數（{totalAdditivesCount}）。
                                  </Text>
                                </View>
                              )}
                              {view.list.length > 0 ? (
                                <IngredientsList ingredients={view.list} />
                              ) : (
                                <View style={[s.tipBox, s.row, { alignItems: 'flex-start' }]}>
                                  <Info size={14} color={TEXT_MID} style={{ marginTop: 2 }} />
                                  <Text style={[s.tipText, { flex: 1 }]}>{view.empty}</Text>
                                </View>
                              )}
                            </View>
                          );
                        })()
                      )}
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
                          {/* 2026-09-13：Cloud 不再回 safety_events_summary
                              （食安管線停用中，產出的只會是沒查證過的安心話）。
                              這段改用本地依事件內容組出的敘述——**有事件才會走到
                              這裡**（整個詳情頁由 hasSafetyEvents 擋住）。 */}
                          {/* 食安事件管線停用中，這一段本來就進不來。
                              原本呼叫的 getAiHistorySummary 會針對具名企業產生
                              憑空的合規背書與違規指控，已於 2026-09-14 移除。 */}
                          <Text style={s.aiText}>食安事件查詢目前停用中。</Text>
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

    </SafeAreaView>
  );
}

// 降階模式用琥珀色：**不可用綠色也不可用紅色**。綠色會讓使用者以為是正常結果、
// 紅色會讓人以為失敗了，而它是「有部分結果但不完整」——第三種狀態要有第三種顏色。
const DEGRADED_FG = '#B45309';
const DEGRADED_BG = '#FEF3C7';
const DEGRADED_BORDER = '#FCD34D';

/**
 * `unavailable_fields` 的中文標籤。
 *
 * 鍵來自 `fog/transforms.py` 的 `DEGRADED_LOCAL_UNAVAILABLE`，
 * 由 `tests/contract/test_degraded_local_contract.py` 跨層比對。
 * 查不到的鍵**原樣顯示**而不是丟掉——後端新增欄位時寧可畫面上出現一個英文字，
 * 也不要安靜少列一項「拿不到的東西」。
 */
const UNAVAILABLE_LABELS: Record<string, string> = {
  health_score: '健康評分',
  risk_level: '風險等級',
  score_breakdown: '評分明細',
  nutrition_facts: '營養標示',
  daily_reference: '每日參考值',
  product_info: '品名與廠商',
  allergen_warnings: '標示上的過敏原警語',
  food_safety_events: '廠商食安事件',
  overall_summary: 'AI 總結',
  additives_summary: 'AI 添加物說明',
};

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
  degradedBox: { gap: 8, backgroundColor: DEGRADED_BG, borderColor: DEGRADED_BORDER },
  degradedTitle: { fontSize: 14 * scale, fontWeight: '900', color: DEGRADED_FG },
  degradedMsg: { fontSize: 12 * scale, color: TEXT_DARK, lineHeight: 18 },
  // 但書用較小字與灰色：它是限制說明，不該跟結果本身搶注意力，
  // 但**必須看得見**——「沒偵測到」在純 OCR 模式下不等於「不含」。
  degradedNote: { fontSize: 11 * scale, color: TEXT_MID, lineHeight: 16, marginTop: 4 },
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
  // 可點的統計卡：邊框加深、底色微調，與不可點的統計卡分得開。
  miniStatTappable: { borderColor: '#D8C9BA', backgroundColor: '#FFFDFB' },
  miniStatHint: { fontSize: 8 * scale, color: TEXT_MID, fontWeight: '700' },
  navTiming: { fontSize: 9 * scale, color: TEXT_MID, marginTop: 2, fontVariant: ['tabular-nums'] },
  catBox: { backgroundColor: '#FBF7F3', borderRadius: 10, padding: 10, marginBottom: 10, gap: 8 },
  catBoxTitle: { fontSize: 10 * scale, fontWeight: '800', color: TEXT_MID, textTransform: 'uppercase' },
  catWrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  catChip: { flexDirection: 'row', alignItems: 'center', gap: 5, backgroundColor: '#fff',
    borderWidth: 1, borderColor: '#E3D3C4', borderRadius: 999, paddingHorizontal: 9, paddingVertical: 4 },
  catChipMuted: { backgroundColor: '#F2ECE6', borderColor: '#DCD1C6' },
  catChipText: { fontSize: 11 * scale, color: TEXT_DARK, fontWeight: '700' },
  catChipNum: { fontSize: 11 * scale, color: '#991b1b', fontWeight: '900' },
  catChipTextMuted: { color: TEXT_MID },
  catNote: { fontSize: 9 * scale, color: TEXT_MID, lineHeight: 13 * scale },
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
  // 出處：比理由再小一級的灰字。它是佐證不是內容，不該跟理由搶注意力，
  // 但必須看得見——「未經人工複核」這五個字是誠實陳述，不是免責聲明。
  personalRiskSource: { fontSize: 10 * scale, color: TEXT_MID, lineHeight: 15, marginTop: 3 },
  // 分數卡下方的可點提示。必須寫出「分數越低越好」——Nutri-Score 的方向
  // 與直覺相反，只給一個「10」會被讀成 0–100 裡的 10 分。
  // ── Nutri-Score 計算明細 ──────────────────────────────────────────────
  breakdownHead: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 },
  breakdownValue: { fontSize: 11 * scale, color: TEXT_MID, fontVariant: ['tabular-nums'] },
  // 占比條：軌道用中性灰，扣分紅、加分綠。這是「這一項用掉上限的幾成」，
  // 不是它在總分裡的權重——兩者不同，描述文字要講清楚。
  barTrack: { height: 5, borderRadius: 999, backgroundColor: 'rgba(0,0,0,0.07)', marginTop: 6, marginBottom: 6, overflow: 'hidden' },
  barFill: { height: 5, borderRadius: 999 },
  barFillRed: { backgroundColor: '#e63e11' },
  barFillGreen: { backgroundColor: '#038141' },
  scoreMax: { fontSize: 9 * scale, color: TEXT_MID, marginTop: -1 },
  totalsRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, marginTop: 8, marginBottom: 2 },
  totalsText: { fontSize: 11 * scale, color: TEXT_MID },
  totalsScore: { fontSize: 13 * scale, fontWeight: '900', color: TEXT_DARK },
  scoreTapRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: 10, paddingHorizontal: 2,
  },
  scoreTapText: { fontSize: 11 * scale, color: TEXT_MID },
  scoreTapLink: { fontSize: 12 * scale, fontWeight: '700', color: BRAND },
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

  // 進階設定頁
  settingsLabel: { fontSize: 12 * scale, fontWeight: '800', color: TEXT_DARK, marginBottom: 4 },
  settingsSub: { fontSize: 11 * scale, color: TEXT_MID, marginBottom: 12 },
});
