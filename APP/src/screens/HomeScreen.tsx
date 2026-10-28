import React, { useState, useEffect, useMemo, useRef } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  View,
  Text,
  ScrollView,
  Pressable,
  ActivityIndicator,
  SafeAreaView,
  KeyboardAvoidingView,
  Platform,
  UIManager,
  StyleSheet,
  Share,
} from 'react-native';
import { Sliders, AlertOctagon, RotateCcw, ArrowLeft, AlertTriangle, Settings2 } from 'lucide-react-native';

import { useFontScale } from '../contexts/FontScaleContext';
import { UserConditions, AnalysisResponse, DegradedLocalResponse } from '../types';
import ResultOverview, { IngredientTab } from '../components/ResultOverview';
import NutritionDetails from '../components/NutritionDetails';
import IngredientExplorer from '../components/IngredientExplorer';
import ProfileSettings, { ProfileValues } from '../components/ProfileSettings';
import ScanDashboard from '../components/ScanDashboard';
import { ScreenHeader, ui as reportUi } from '../components/reportUi';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';
import { loadHistory, recordScan, clearHistory, HistoryEntry } from '../utils/scanHistory';
import type { ScrollViewInstance } from 'react-native';
import CompetitionDemoPanel from '../components/CompetitionDemoPanel';
import { COMPETITION_DEMO_CASES, COMPETITION_DEMO_MODES, CompetitionDemoMode } from '../competition/demoCases';
import IngredientsList from '../components/IngredientsList';
import DropdownEvent from '../components/DropdownEvent';
import PackageImageScanner from '../components/PackageImageScanner';
import BarcodeScanner from '../components/BarcodeScanner';
import { analyzePersonalRisks, getProductAllergenWarnings } from '../utils/personalization';
import { FOG_URL, CLOUD_URL, ANALYSIS_TIMEOUT_MS } from '../constants/endpoints';
import * as Telemetry from '../utils/telemetry';
import { pipelineEvent } from '../utils/pipelineDashboard';
import { useQueuedAnalysis } from '../hooks/useQueuedAnalysis';
import { QueuedAnalysis, queuedElapsedMs, responseBody, isAnalysisResult, taskKey } from '../utils/queuedAnalysis';
import { buildAnalysisRequest } from '../utils/analysisRequest';


if (Platform.OS === 'android') {
  UIManager.setLayoutAnimationEnabledExperimental?.(true);
}

// ─── 畫面縮放 ─────────────────────────────────────────────────────────────────
const MIN_ZOOM = 1;
const MAX_ZOOM = 4;

/** 下限是 1：縮到比原尺寸小沒有用途，只會在四周留白。 */
const clampZoom = (value: number) => {
  'worklet';
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
};

/**
 * 夾住位移，讓放大後的內容永遠蓋滿可視區。
 *
 * 放大 s 倍後內容比可視區多出 size*(s-1)，往任一邊最多只能移動它的一半；
 * 超過就會把內容邊緣拖進畫面，露出後面的底色。
 */
const clampPan = (value: number, scale: number, size: number) => {
  'worklet';
  const max = (size * (scale - 1)) / 2;
  return Math.min(max, Math.max(-max, value));
};


// ─── Component ────────────────────────────────────────────────────────────────
export default function HomeScreen() {
  const { fontScale, toggleFontScale, isLarge } = useFontScale();
  // createStyles 會重建整份 StyleSheet，掛 useMemo 之後只有倍率真的變了才重建，
  // 而不是每次 setState 都跑一遍。
  const s = useMemo(() => createStyles(fontScale), [fontScale]);

  // ── 畫面縮放（雙指）────────────────────────────────────────────────────────
  // 放大的是整個畫面，不是字級：版面完全不重排，只是等比拉大，因此圖表、圓環、
  // 表格的相對位置都跟原本一模一樣，放大只是為了看清楚。字級調整是另一回事，
  // 仍由右下角的「Aa」負責。
  //
  // 全程都在 UI 執行緒的 shared value 上算，不碰 React state——這一頁的
  // createStyles 很重，若縮放途中觸發重新渲染會直接掉幀。
  const zoomScale = useSharedValue(1);
  const zoomX = useSharedValue(0);
  const zoomY = useSharedValue(0);
  // 可視區尺寸由 onLayout 量到，別用螢幕尺寸推——上面還有安全區與浮動按鈕。
  const viewW = useSharedValue(0);
  const viewH = useSharedValue(0);
  // 兩個手勢各自記上一幀的值，用「增量」而不是「相對起手的絕對值」更新。
  // 這樣縮放與拖曳同時進行時不會互相覆蓋對方寫進去的位移。
  const prevPinchScale = useSharedValue(1);
  const prevPanX = useSharedValue(0);
  const prevPanY = useSharedValue(0);
  const prevSwipeX = useSharedValue(0);

  const zoomGesture = useMemo(() => {
    const pinch = Gesture.Pinch()
      .onStart(() => {
        prevPinchScale.value = 1;
      })
      .onUpdate(e => {
        const factor = e.scale / prevPinchScale.value;
        prevPinchScale.value = e.scale;
        const next = clampZoom(zoomScale.value * factor);
        // 夾到上下限後真正生效的倍率，可能小於手指給的——位移要用生效的那個，
        // 否則撐到頂之後畫面還會繼續飄。
        const applied = next / zoomScale.value;
        const fx = e.focalX - viewW.value / 2;
        const fy = e.focalY - viewH.value / 2;
        // 把兩指中心底下的那一點釘住，放大才是「從手指的位置展開」。
        zoomX.value = clampPan(fx - (fx - zoomX.value) * applied, next, viewW.value);
        zoomY.value = clampPan(fy - (fy - zoomY.value) * applied, next, viewH.value);
        zoomScale.value = next;
      })
      .onEnd(() => {
        // 縮回原尺寸就順手回正，免得留著一個看不出來的偏移。
        if (zoomScale.value <= MIN_ZOOM + 0.01) {
          zoomScale.value = withTiming(MIN_ZOOM, { duration: 160 });
          zoomX.value = withTiming(0, { duration: 160 });
          zoomY.value = withTiming(0, { duration: 160 });
        }
      });

    // 兩指拖曳平移，兩個方向都能動。
    const pan = Gesture.Pan()
      .minPointers(2)
      .onStart(() => {
        prevPanX.value = 0;
        prevPanY.value = 0;
      })
      .onUpdate(e => {
        const dx = e.translationX - prevPanX.value;
        const dy = e.translationY - prevPanY.value;
        prevPanX.value = e.translationX;
        prevPanY.value = e.translationY;
        zoomX.value = clampPan(zoomX.value + dx, zoomScale.value, viewW.value);
        zoomY.value = clampPan(zoomY.value + dy, zoomScale.value, viewH.value);
      });

    // 單指左右平移。垂直方向刻意讓它失敗，把上下完整留給 ScrollView——這一頁很長，
    // 放大時不能捲頁會很難用；而垂直看不到的部分本來就能靠捲動帶進畫面。
    //
    // 不需要另外判斷「有沒有放大」：1 倍時 clampPan 的上限是 0，橫拖算出來的位移
    // 一律是 0，等於沒作用。少一個要跟 UI 執行緒同步的開關。
    const swipe = Gesture.Pan()
      .maxPointers(1)
      // 先橫移超過 10px 才接手，縱向超過 8px 就放棄。兩個門檻一起決定了
      // 「這一下是平移還是捲動」，沒有這組判斷會兩邊搶同一個手勢。
      .activeOffsetX([-10, 10])
      .failOffsetY([-8, 8])
      .onStart(() => {
        prevSwipeX.value = 0;
      })
      .onUpdate(e => {
        const dx = e.translationX - prevSwipeX.value;
        prevSwipeX.value = e.translationX;
        zoomX.value = clampPan(zoomX.value + dx, zoomScale.value, viewW.value);
      });

    return Gesture.Simultaneous(pinch, pan, swipe);
  }, []);

  const zoomStyle = useAnimatedStyle(() => ({
    transform: [
      { translateX: zoomX.value },
      { translateY: zoomY.value },
      { scale: zoomScale.value },
    ],
  }));


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

  const [barcodeInput, setBarcodeInput] = useState('');
  const [uploadedImages, setUploadedImages] = useState<string[]>([]);
  const [serverEndpoint, setServerEndpoint] = useState<'fog' | 'cloud'>('fog');

  // 其他成分預設收合：使用者要看的是添加物，其餘配料是備查用的
  const [scannerVisible, setScannerVisible] = useState(false);

  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisResult, setAnalysisResult] = useState<AnalysisResponse | null>(null);
  const [dashboardReceiptId, setDashboardReceiptId] = useState<string | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [completedQueuedTask, setCompletedQueuedTask] = useState<QueuedAnalysis | null>(null);
  const recordQueueOutcome = (task: QueuedAnalysis, result: AnalysisResponse | undefined, error: string | null) => {
    const elapsed = queuedElapsedMs(task);
    if (elapsed === null) return; // Device clock moved backwards; don't invent a duration.
    void Telemetry.append({
      phase: error ? 'queue_failed' : 'queue_completed', request_id: task.requestId,
      at: new Date().toISOString(), endpoint: 'fog', barcode: task.barcode,
      n_images: task.nImages, payload_chars: task.payloadChars, http_status: null,
      total_ms: elapsed, fog_node: null, fog_py: null, cloud: null, cache: 'QUEUED',
      bypass_cache: task.bypassCache, status: error ? 'queue_failed' : 'queue_completed',
      health_score: result?.health_score ?? null, risk_level: result?.risk_level ?? null,
      n_additives: result?.ingredients_detail?.length ?? null, degraded: false, error,
    });
  };
  const queued = useQueuedAnalysis({
    restored: () => setView('result'),
    completed: (task, job) => {
      if (!isAnalysisResult(job.result)) {
        queued.pause();
        const returnedMessage = (job.result as { message?: unknown } | undefined)?.message;
        const message = typeof returnedMessage === 'string' ? returnedMessage : '雲端已處理此任務，但未能產生有效分析結果。';
        setAnalysisError(message);
        recordQueueOutcome(task, undefined, message);
        return;
      }
      const requestId = job.request_id && /^[A-Za-z0-9_.:-]{1,96}$/.test(job.request_id)
        ? job.request_id : task.requestId;
      setCompletedQueuedTask({ ...task, requestId });
      setLastTiming({ totalMs: queuedElapsedMs(task), cloudMs: null, cached: false, queued: true });
      setDashboardReceiptId(requestId);
      setAnalysisError(null);
      setResultIsDemo(false); setResultPhoto(undefined); setDegradedResult(null); setActiveDetailView(null);
      void recordScan(job.result).then(setHistory);
      setAnalysisResult(job.result);
      setView('result');
    },
    failed: (task, message) => {
      setAnalysisError(message);
      recordQueueOutcome(task, undefined, message);
      void pipelineEvent(task.requestId, 'error', 'failed', { message });
    },
  });
  useEffect(() => {
    if (!dashboardReceiptId || !analysisResult || view !== 'result' || isAnalyzing || analysisError) return;
    // Runs after the result state has committed; the dashboard does not invent an ACK.
    const frame = requestAnimationFrame(() => {
      void pipelineEvent(dashboardReceiptId, 'sync', 'done', {
        result: { product_name: analysisResult.product_info?.name,
                  health_score: analysisResult.health_score },
      });
      if (completedQueuedTask) {
        recordQueueOutcome(completedQueuedTask, analysisResult, null);
        void queued.dismiss(completedQueuedTask);
        setCompletedQueuedTask(null);
      }
      setDashboardReceiptId(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [dashboardReceiptId, analysisResult, view, isAnalyzing, analysisError]);
  // Fog 在 Cloud 連不上且無快取時，用本機 OCR 回的部分結果。
  // **與 analysisResult 分開存**：它刻意沒有 health_score，混在一起會讓
  // 結果頁的 `health_score ?? 100` 顯示一個不存在的分數
  // （`normalize_result` 憑空補 75 分那次就是這樣來的）。
  const [degradedResult, setDegradedResult] = useState<DegradedLocalResponse | null>(null);
  // 測試模式：送 X-Bypass-Cache，讓 Fog 跳過**讀**快取（照常寫）。
  // 量延遲時每一次都要走完整路徑，命中快取的那幾次會把中位數拉到失真。
  const [bypassCache, setBypassCache] = useState(false);
  const [activeDetailView, setActiveDetailView] = useState<'additives' | 'history' | 'breakdown' | null>(null);
  // 這一次分析花了多久。**手機自己量的**（同一個時鐘兩次相減），
  // 不是把各層的時間戳相減——三層分處不同機器，時鐘不保證同步。
  // 這個數字包含網路往返，是使用者實際等待的時間。
  const [lastTiming, setLastTiming] = useState<
    { totalMs: number | null; cloudMs: number | null; cached: boolean; queued?: boolean } | null>(null);


  const [resultPhoto, setResultPhoto] = useState<string | undefined>();
  const [resultIsDemo, setResultIsDemo] = useState(false);
  const [ingredientTab, setIngredientTab] = useState<IngredientTab>('all');
  const [profileReturnView, setProfileReturnView] = useState<'scan' | 'result'>('scan');
  const scrollRef = useRef<ScrollViewInstance>(null);
  const scrollToTop = () => requestAnimationFrame(() => scrollRef.current?.scrollTo({ y: 0, animated: false }));
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [demoMode, setDemoMode] = useState<CompetitionDemoMode | null>(null);
  useEffect(() => { void loadHistory().then(setHistory); }, []);
  useEffect(() => { scrollToTop(); }, [view, activeDetailView]);

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleQuickPreview = (code: string) => {
    const example = COMPETITION_DEMO_CASES.find(item => item.id === code);
    if (!example) return;
    setResultPhoto(undefined); setResultIsDemo(true); setLastTiming(null);
    setActiveDetailView(null); setDegradedResult(null);
    queued.pause();
    setDashboardReceiptId(null);
    setCompletedQueuedTask(null);
    const data = example.result;
    setAnalysisResult(data);
    setAnalysisError(null);
    setIsAnalyzing(false);
    setView('result');
  };


  const handleSubmitAnalysis = async () => {
    if (!barcodeInput && uploadedImages.length === 0) return;

    setResultPhoto(uploadedImages[0] ? (uploadedImages[0].startsWith('data:') ? uploadedImages[0] : `data:image/jpeg;base64,${uploadedImages[0]}`) : undefined);
    setResultIsDemo(false); setActiveDetailView(null);
    setIsAnalyzing(true);
    setAnalysisResult(null);
    setAnalysisError(null);
    setDegradedResult(null);
    queued.pause();
    setCompletedQueuedTask(null);
    setLastTiming(null);
    setView('result');

    // ── 實驗埋點 ────────────────────────────────────────────────────────
    // request id 逐層轉發，讓 App／Fog／Cloud 三份紀錄併得起來。
    // t0 只與 t1 相減（同一個時鐘），不與任何伺服器時刻比對——決策單 #15
    // 未定的正是跨機器時鐘校正，這裡整個繞過。
    // ⚠ 宣告在 try 之外：**失敗與降階那幾次才是最需要量的**，
    //   放在 try 裡面 finally 就取不到，那些次會剛好沒有紀錄。
    const reqId = Telemetry.newRequestId();
    setDashboardReceiptId(null);
    if (uploadedImages.length > 0) void pipelineEvent(reqId, 'image', 'done', {
      ...(uploadedImages[0] ? { source_image: uploadedImages[0] } : {}),
      image_count: uploadedImages.length,
      ...(demoMode && serverEndpoint === 'fog' ? { demo_scenario: demoMode } : {}),
    });
    const payloadChars = uploadedImages.reduce((n, b) => n + b.length, 0);
    const t0 = Date.now();
    let httpStatus: number | null = null;
    let respHeaders: Headers | null = null;
    let recResult: any = null;
    let recError: string | null = null;
    let submittedBarcode = barcodeInput.trim();

    try {
      const payload = buildAnalysisRequest(barcodeInput, uploadedImages);
      submittedBarcode = payload.barcode;
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
            ...(demoMode && serverEndpoint === 'fog' ? { 'X-Demo-Scenario': demoMode } : {}),
            // 專用標頭而不是沿用上面那個 Cache-Control——它每次都送，
            // 拿它當判準等於永久關閉快取，就量不出快取有沒有幫上忙。
            ...(bypassCache ? { 'X-Bypass-Cache': '1' } : {}),
          },
          signal: ctrl.signal,
          // 個人化比對自 2026-08-04 起完全在本地進行，健康背景不再送往後端。
          body: JSON.stringify(payload),
        });
      } finally {
        // 成功時也要清掉，否則這個 timer 會一直活到 120 秒後才觸發 abort()，
        // 對已完成的請求沒作用但會讓測試環境留著未回收的計時器。
        clearTimeout(timer);
      }
      httpStatus = response.status;
      respHeaders = response.headers;
      const json = await responseBody(response);
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
      if (result?.status === 'queued' && result?.queue_id) {
        recResult = result;
        await queued.remember({ id: result.queue_id, message: result.message ?? '資料已暫存在 Fog。',
          requestId: result.request_id || reqId, endpoint: API_URL.replace(/\/query\/?$/, ''),
          submittedAt: t0, barcode: submittedBarcode, nImages: uploadedImages.length,
          payloadChars, bypassCache });
        return;
      }

      if (result?.status === 'degraded' && result?.degraded_mode === 'local_ocr') {
        recResult = result;
        setDegradedResult(result as DegradedLocalResponse);
        return;
      }

      if (!isAnalysisResult(result)) {
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
      console.log('[Fog] score_breakdown:', JSON.stringify(Array.isArray(result.score_breakdown)
        ? result.score_breakdown.slice(0, 5) : result.score_breakdown));
      // ────────────────────────────────────────────────────────────────────

      recResult = result;
      void pipelineEvent(reqId, 'sync', 'active', {
        result: { product_name: result.product_info?.name, health_score: result.health_score },
        cached: result.cached,
      });
      setDashboardReceiptId(reqId);
      void recordScan(result).then(setHistory);
      setAnalysisResult(result);
    } catch (err: any) {
      // AbortError 要單獨講：「連不上」與「等太久」對使用者是不同的下一步，
      // 前者去檢查網路或伺服器，後者重試或少拍幾張就好。
      const msg = err?.name === 'AbortError'
        ? `分析逾時（超過 ${ANALYSIS_TIMEOUT_MS / 1000} 秒）。請確認網路後重試，或減少照片張數。`
        : err?.message ?? '無法連線至後端分析節點，請確認伺服器有正常運作！';
      recError = msg;
      void pipelineEvent(reqId, 'error', 'failed', { message: msg });
      setAnalysisError(msg);
    } finally {
      setIsAnalyzing(false);
      {
        // Cloud 自報的分析耗時（X-Timing-Cloud 的 total）。與手機量到的差額
        // 就是網路與各層轉發——兩個都顯示，使用者才看得出慢在哪一段。
        const cloud = Telemetry.parseTiming(respHeaders?.get('X-Timing-Cloud') ?? null);
        if (recResult?.status !== 'queued') setLastTiming({
          totalMs: Date.now() - t0,
          cloudMs: cloud?.total ?? null,
          // 快取命中時耗時會低一個量級，不標出來會讓人以為辨識變快了。
          cached: (respHeaders?.get('X-Cache') ?? '').toLowerCase().includes('hit'),
        });
      }
      // 一次掃描一列。**刻意不記照片與成分原文**——一是隱私（紀錄會被匯出），
      // 二是能拿來算指標的是「有幾項」而不是內容。
      void Telemetry.append({
        phase: 'response',
        request_id: reqId,
        at: new Date().toISOString(),
        endpoint: serverEndpoint,
        barcode: submittedBarcode,
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
    setResultPhoto(undefined); setResultIsDemo(false);
    queued.pause();
    setDashboardReceiptId(null);
    setCompletedQueuedTask(null);
    setView('scan');
    setAnalysisResult(null);
    setAnalysisError(null);
    setDegradedResult(null);
    setActiveDetailView(null);
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

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={s.root}>
      {/* 浮動控制項刻意留在縮放層外面：跟著一起放大的話，拉到 4 倍時它們會被
          推出畫面，使用者就回不去了。 */}
      <Pressable accessibilityRole="button" accessibilityLabel={isLarge ? '使用標準字級' : '放大字級'} onPress={toggleFontScale} style={[s.fontScaleBtn, isLarge && s.fontScaleBtnActive]}>
        <Text style={[s.fontScaleBtnText, isLarge && s.fontScaleBtnTextActive]}>Aa</Text>
      </Pressable>

      {/* 健康設定入口。設定畫面本身不顯示，避免與畫面內的返回鍵重複。 */}
      {view !== 'profile' && view !== 'settings' && (
        <Pressable
          onPress={() => { setProfileReturnView(view === 'result' ? 'result' : 'scan'); setView('profile'); }}
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
      {/* GestureDetector 掛在沒有被 transform 的那一層，手勢回報的焦點座標才會是
          可視區座標；掛在縮放層上的話座標會跟著一起縮，錨點就對不準。 */}
      <GestureDetector gesture={zoomGesture}>
        <View
          style={s.zoomViewport}
          onLayout={e => {
            viewW.value = e.nativeEvent.layout.width;
            viewH.value = e.nativeEvent.layout.height;
          }}
        >
          <Animated.View style={[s.zoomLayer, zoomStyle]}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView ref={scrollRef} contentContainerStyle={s.scroll} keyboardShouldPersistTaps="handled">

          {(view === 'scan' || view === 'result') && queued.storageWarning !== '' && (
            <View style={[s.card, s.errorBox]}><Text style={s.settingsSub}>{queued.storageWarning}</Text></View>
          )}
          {(view === 'scan' || view === 'result') && queued.tasks.length > 0 && !isAnalyzing && (
            <View style={s.card}>
              <Text style={s.loadingTitle}>已保存的分析任務（{queued.tasks.length}）</Text>
              <Text style={s.settingsSub}>可恢復查詢。移除手機紀錄不會取消 Fog 的分析工作。</Text>
              {queued.tasks.map(task => (
                <View key={taskKey(task)} style={{ marginTop: 12, gap: 7 }}>
                  <Text selectable style={s.settingsSub}>{task.barcode || '照片掃描'} · {task.id}</Text>
                  <View style={[s.row, { gap: 10 }]}>
                    <Pressable style={s.btnSecondary} onPress={() => {
                      setAnalysisResult(null); setAnalysisError(null); setDegradedResult(null);
                      setCompletedQueuedTask(null); setDashboardReceiptId(null); setLastTiming(null);
                      queued.select(task); setView('result');
                    }}><Text style={s.btnSecondaryText}>恢復查詢</Text></Pressable>
                    <Pressable style={s.btnSecondary} onPress={() => { void queued.dismiss(task); }}>
                      <Text style={s.btnSecondaryText}>移除手機紀錄</Text>
                    </Pressable>
                  </View>
                </View>
              ))}
            </View>
          )}

          {/* ══════════ 健康設定（由右下角齒輪進入）══════════ */}
          {view === 'profile' && (
            <ProfileSettings
              initial={{ group: targetGroup, allergens, chronic: chronicDiseases, customAllergen }}
              onBack={() => setView(profileReturnView)}
              onSave={async (values: ProfileValues) => {
                await AsyncStorage.multiSet([
                  ['targetGroup', values.group], ['allergens', JSON.stringify(values.allergens)],
                  ['chronicDiseases', JSON.stringify(values.chronic)], ['customAllergen', values.customAllergen],
                ]);
                setTargetGroup(values.group); setAllergens(values.allergens);
                setChronicDiseases(values.chronic); setCustomAllergen(values.customAllergen);
                setView(profileReturnView);
              }}
            />
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
              <Text style={s.settingsSub}>選擇分析請求要送往哪個後端節點</Text>
              <View style={s.segmentedControl}>
                <Pressable
                  style={[s.segBtn, serverEndpoint === 'fog' && s.segBtnActive]}
                  onPress={() => { setServerEndpoint('fog'); setDemoMode(null); }}
                >
                  <Text style={[s.segBtnText, serverEndpoint === 'fog' && s.segBtnTextActive]}>
                    微型邊緣霧節點 (Fog)
                  </Text>
                </Pressable>
                <Pressable
                  style={[s.segBtn, serverEndpoint === 'cloud' && s.segBtnActiveCloud]}
                  onPress={() => { setServerEndpoint('cloud'); setDemoMode(null); }}
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
                  onPress={() => { setBypassCache(false); setDemoMode(null); }}
                >
                  <Text style={[s.segBtnText, !bypassCache && s.segBtnTextActive]}>
                    使用快取
                  </Text>
                </Pressable>
                <Pressable
                  style={[s.segBtn, bypassCache && s.segBtnActiveCloud]}
                  onPress={() => { setBypassCache(true); setDemoMode(null); }}
                >
                  <Text style={[s.segBtnText, bypassCache && s.segBtnTextActive]}>
                    每次重新辨識
                  </Text>
                </Pressable>
              </View>

              <Text style={[s.settingsLabel, { marginTop: 24 }]}>離線處理</Text>
              <Text style={s.settingsSub}>
                Fog 優先使用本地快取。沒有快取且無法連線雲端時，照片會暫存在 Fog，
                待連線恢復後依序分析。請等候排隊結果，不必重複上傳。
              </Text>

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
              <BarcodeScanner visible={scannerVisible} onClose={() => setScannerVisible(false)}
                onBarcodeScanned={code => { setBarcodeInput(code); setScannerVisible(false); }} />
              <ScanDashboard barcode={barcodeInput} onBarcodeChange={setBarcodeInput}
                onBarcodeScan={() => setScannerVisible(true)}
                imagePicker={<PackageImageScanner onImageCaptured={base64 => setUploadedImages(previous => [...previous, base64])}
                  images={uploadedImages} onRemoveImage={index => setUploadedImages(previous => previous.filter((_, i) => i !== index))} />}
                canAnalyze={!!barcodeInput || uploadedImages.length > 0} onAnalyze={handleSubmitAnalysis}
                history={history} onClearHistory={() => { clearHistory(); setHistory([]); }}
                onHistory={entry => {
                  queued.pause(); setDashboardReceiptId(null); setCompletedQueuedTask(null); setLastTiming(null);
                  setAnalysisResult(entry.result); setResultPhoto(undefined); setResultIsDemo(false);
                  setDegradedResult(null); setAnalysisError(null); setIsAnalyzing(false);
                  setActiveDetailView(null); setView('result');
                }}
                onDemo={handleQuickPreview} onSettings={() => setView('settings')}
                demoContent={<CompetitionDemoPanel onPreview={handleQuickPreview} mode={demoMode} onMode={mode => {
                  const preset = COMPETITION_DEMO_MODES.find(item => item.id === mode)!;
                  setServerEndpoint('fog'); setBypassCache(preset.bypassCache); setDemoMode(mode);
                }} />}
                endpointLabel={serverEndpoint === 'fog' ? 'Fog 節點' : 'Cloud 節點'} />
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
                    正在查詢 Fog 快取，或透過雲端辨識標示與解釋成分，請稍候。
                  </Text>
                </View>
              )}

                {!isAnalyzing && queued.current && !completedQueuedTask && !analysisResult && !analysisError && (
                <View style={s.card}>
                  <ActivityIndicator color="#2563eb" />
                  <Text style={s.loadingTitle}>已暫存，等待雲端分析</Text>
                  <Text style={s.settingsSub}>{queued.notice || queued.current.message}</Text>
                  <Text style={s.settingsSub}>連線恢復後自動依序處理；此頁會定期查詢結果。</Text>
                  <Text style={s.settingsSub}>任務編號：{queued.current.id}</Text>
                  <Pressable style={s.btnSecondary} onPress={handleBackToScan}><Text style={s.btnSecondaryText}>返回掃描，保留任務</Text></Pressable>
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
                        耗時 {((lastTiming.totalMs ?? 0) / 1000).toFixed(1)} 秒
                      </Text>
                    )}
                  </View>

                  <View style={[s.card, s.degradedBox]}>
                    <View style={s.row}>
                      <AlertTriangle size={20} color={DEGRADED_FG} />
                      <Text style={s.degradedTitle}>
                        {'舊版辨識・部分結果'}
                      </Text>
                    </View>
                    {/* 使用者自己選的模式，不該用故障的語氣講。Fog 回的 message
                        是為「雲端掛了」寫的，這裡覆蓋掉。 */}
                    <Text style={s.degradedMsg}>
                      {degradedResult.message}
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

              {/* Results: overview first, complete information on focused detail screens. */}
              {!isAnalyzing && !analysisError && !degradedResult && analysisResult && (
                activeDetailView === null ? (
                  <ResultOverview result={analysisResult} photoUri={resultPhoto} isDemo={resultIsDemo}
                    matchedAllergens={matchedAllergens} generalWarnings={generalAllergenWarnings}
                    onBack={handleBackToScan}
                    onScanNext={() => { setUploadedImages([]); setBarcodeInput(''); handleBackToScan(); }}
                    onNutrition={() => setActiveDetailView('breakdown')}
                    onIngredients={tab => { setIngredientTab(tab); setActiveDetailView('additives'); }}
                    onHistory={hasSafetyEvents ? () => setActiveDetailView('history') : undefined}
                    additionalWarnings={<>
                      {lastTiming && !resultIsDemo && <Text style={s.navTiming}>
                        {lastTiming.queued ? '送出至取得結果（含排隊）' : '本次分析耗時'}：{lastTiming.totalMs === null ? '時間不明' : `${(lastTiming.totalMs / 1000).toFixed(1)} 秒`}
                      </Text>}
                      {personalAdditiveRisks.length > 0 && <View style={[reportUi.card, { backgroundColor: '#FFF9EC', gap: 8 }]}>
                        <View style={reportUi.row}><AlertTriangle size={18} color="#AD762C" /><Text style={[reportUi.sectionTitle, { fontSize: 15 * fontScale }]}>個人化添加物提醒</Text></View>
                        {personalAdditiveRisks.map((risk, index) => <View key={index} style={{ gap: 3 }}>
                          <Text style={{ fontWeight: '700', fontSize: 13 * fontScale, color: '#6D552F' }}>{risk.name}</Text>
                          <Text style={{ fontSize: 12 * fontScale, lineHeight: 20 * fontScale, color: '#857658' }}>{risk.reason}</Text>
                          {risk.reviewedByHuman === false && <Text style={{ fontSize: 11 * fontScale, color: '#9A8D76' }}>未經人工複核・來源請見成分詳情</Text>}
                        </View>)}
                      </View>}
                      {nutritionWarnings.length > 0 && <View style={[reportUi.card, { backgroundColor: '#FFF9EC', gap: 8 }]}>
                        <Text style={[reportUi.sectionTitle, { fontSize: 15 * fontScale }]}>個人化營養提醒</Text>
                        {nutritionWarnings.map((warning, index) => <Text key={index} style={{ fontSize: 13 * fontScale, lineHeight: 21 * fontScale, color: '#857658' }}>{warning.condition}・{warning.title}：{warning.detail}</Text>)}
                      </View>}
                    </>}
                  />
                ) : activeDetailView === 'breakdown' ? (
                  <NutritionDetails result={analysisResult} onBack={() => setActiveDetailView(null)} />
                ) : activeDetailView === 'additives' ? (
                  <IngredientExplorer key={ingredientTab} initialTab={ingredientTab} ingredients={allIngredients}
                    onBack={() => setActiveDetailView(null)} onNavigate={scrollToTop} />
                ) : (
                  <View style={reportUi.stack}>
                    <ScreenHeader title="廠商食安事件" onBack={() => setActiveDetailView(null)} />
                    {safeFoodSafetyEvents.map((event, index) => <DropdownEvent key={index} event={event} index={index} />)}
                  </View>
                )
              )}
            </View>
          )}

          <View style={{ height: 40 }} />
        </ScrollView>
      </KeyboardAvoidingView>
          </Animated.View>
        </View>
      </GestureDetector>

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
  scroll: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 120, gap: 0, width: '100%', maxWidth: 680, alignSelf: 'center' },

  // 雙指縮放。overflow: hidden 是必要的——放大後內容會超出這一層的邊界，
  // 不裁掉會蓋到浮動按鈕。
  zoomViewport: { flex: 1, overflow: 'hidden' },
  zoomLayer: { flex: 1 },

  // 最近查過
  historyBox: { marginTop: 20, gap: 2 },
  historyHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginBottom: 4,
  },
  historyTitle: {
    fontSize: 10 * scale, fontWeight: '800', color: TEXT_DARK,
    textTransform: 'uppercase', letterSpacing: 0.6,
  },
  historyClear: { fontSize: 11 * scale, color: TEXT_MID, fontWeight: '600' },
  historyRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 10,
    borderBottomWidth: 1, borderBottomColor: 'rgba(200,184,154,0.4)',
  },
  historyRowMain: { flex: 1, gap: 2 },
  historyName: { fontSize: 13 * scale, fontWeight: '700', color: TEXT_DARK },
  historyMeta: { fontSize: 10 * scale, color: TEXT_MID },
  historyGrade: {
    minWidth: 22, paddingHorizontal: 6, paddingVertical: 2, borderRadius: 6,
    backgroundColor: BRAND_LIGHT, borderWidth: 1, borderColor: BRAND,
    alignItems: 'center',
  },
  historyGradeText: { fontSize: 11 * scale, fontWeight: '800', color: BRAND },

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
