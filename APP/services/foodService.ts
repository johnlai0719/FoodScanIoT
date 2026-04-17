import axios from "axios";
import { ScanResult, IARCCategory, UserProfile, Ingredient } from "../types";

// 【關鍵設定】優先讀取環境變數 (用於 Cloudflare Tunnel Demo)
// 若無環境變數，則回退到開發用的實體 IP
const FOG_API_URL = process.env.EXPO_PUBLIC_FOG_URL || "https://safeog.dev"; 

const fogCache: Record<string, ScanResult> = {};

function adaptBackendData(data: any): ScanResult {
  // 【最核心除錯】印出原始資料，確認到底是哪一層出錯
  console.log("[DEBUG] Raw Data from Fog:", JSON.stringify(data).substring(0, 200));

  // 核心修復：深層挖掘資料
  const report = data.data && data.status === 'success' ? data.data : (data.status === 'success' ? data : data);
  
  // 取得名稱：嘗試所有可能的路徑
  const name = report.product_info?.name || report.name || report.product_name || report.barcode || "AI 解析產品";
  const brand = report.product_info?.brand || report.brand || "未知品牌";
  
  const ingredientsDetail: Ingredient[] = [];

  // 嘗試從多種可能的欄位讀取成份
  const rawIngredients = report.ingredients_detail || report.ingredients || [];
  if (Array.isArray(rawIngredients)) {
    rawIngredients.forEach((ing: any) => {
      if (typeof ing === 'string') {
        ingredientsDetail.push({
          name: ing,
          isAdditive: false,
          isAllergen: false,
          description: "一般成分",
          groupRisks: []
        });
      } else {
        ingredientsDetail.push({
          name: ing.name || "未知成分",
          isAdditive: !!ing.isAdditive,
          isAllergen: !!ing.isAllergen,
          iarcRating: ing.iarcRating || ing.iarc_class || "",
          adiValue: ing.adiValue || ing.adi || "",
          purpose: ing.purpose || ing.food_tech_purpose || "",
          caution: ing.caution || ing.medical_caution || "",
          description: ing.description || (ing.isAdditive ? "食品添加物" : "一般成分"),
          groupRisks: ing.groupRisks || ing.risks || []
        });
      }
    });
  }

  // 備援方案：支援舊格式 (chemical_additives / basic_ingredients)
  if (ingredientsDetail.length === 0) {
    if (Array.isArray(report.chemical_additives)) {
      report.chemical_additives.forEach((ing: any) => {
        ingredientsDetail.push({
          name: typeof ing === 'string' ? ing : (ing.name || "添加物"),
          isAdditive: true,
          isAllergen: false,
          description: ing.purpose || "食品添加物",
          groupRisks: []
        });
      });
    }
    if (Array.isArray(report.basic_ingredients)) {
      report.basic_ingredients.forEach((ing: any) => {
        ingredientsDetail.push({
          name: typeof ing === 'string' ? ing : (ing.name || "成分"),
          isAdditive: false,
          isAllergen: false,
          description: "一般成分",
          groupRisks: []
        });
      });
    }
  }

  const diagnosis = report.final_health_diagnosis || report.diagnosis || {};
  const nutrition = report.nutrition_facts || report.nutrition || {};

  return {
    product_info: {
      barcode: report.product_info?.barcode || report.barcode || "Unknown",
      name: name,
      brand: brand,
      manufacturer: report.product_info?.manufacturer || report.manufacturer || "未知廠商",
      allergens: report.product_info?.allergens || report.allergens || "",
      processingLevel: report.product_info?.processingLevel || (report.risk_level === "low" ? "Processed" : "Ultra-Processed")
    },
    ingredients_detail: ingredientsDetail,
    nutrition_facts: {
      calories: nutrition.calories || 0,
      protein: nutrition.protein || 0,
      fat: nutrition.fat || 0,
      carbohydrates: nutrition.carbohydrates || 0,
      sugar: nutrition.sugar || 0,
      sodium: nutrition.sodium || 0,
      servingSize: nutrition.servingSize || "每 100g"
    },
    final_health_diagnosis: {
      grade: diagnosis.grade || (report.health_score > 70 ? "A" : (report.health_score > 50 ? "B" : "C")),
      score: diagnosis.score || report.health_score || 0,
      transparencyScore: diagnosis.transparencyScore || 90,
      summary: diagnosis.summary || report.personalized_notes?.[0] || "分析完成。",
      warnings: diagnosis.warnings || [],
      score_breakdown: diagnosis.score_breakdown || [],
      groupRiskSummary: diagnosis.groupRiskSummary || [],
      evidence_chain: diagnosis.evidence_chain || []
    },
    manufacturer_alerts: report.manufacturer_alerts || report.alerts || report.product_info?.manufacturer_alerts || [],
    certification_marks: report.certification_marks || report.marks || report.product_info?.certification_marks || [],
    source: "Fog Node (Edge)",
    latency: 0
  };
}

export async function clearFogCache(): Promise<boolean> {
  try {
    console.log(`[DEBUG] Attempting to clear cache at: ${FOG_API_URL}/cache`);
    await axios.delete(`${FOG_API_URL}/cache`, { timeout: 5000 });
    Object.keys(fogCache).forEach(key => delete fogCache[key]);
    return true;
  } catch (err: any) {
    console.error(`[ERROR] Cache clear failed. Target: ${FOG_API_URL}. Message: ${err.message}`);
    return false;
  }
}

export async function fetchFromFog(barcode: string, userProfile?: UserProfile, imageDatas?: string[], retryCount = 1): Promise<ScanResult> {
  const startTime = Date.now();

  if (fogCache[barcode] && (!imageDatas || imageDatas.length === 0)) {
    return { ...fogCache[barcode], latency: Date.now() - startTime };
  }

  const payload = {
    barcode,
    label_images: imageDatas || [],
    user_conditions: {
      group: (userProfile as any)?.group || "adult",
      allergens: [
        ...((userProfile as any)?.allergens || []),
        ...((userProfile as any)?.custom_allergens || [])
      ],
      health_conditions: (userProfile as any)?.healthConditions || []
    }
  };

  for (let attempt = 0; attempt <= retryCount; attempt++) {
    try {
      const isLargeRequest = barcode === "TEST" || (imageDatas && imageDatas.length > 0);
      const currentTimeout = isLargeRequest ? 60000 : 15000; // 測試或影像請求給予 60 秒，一般條碼維持 15 秒
      
      console.log(`[DEBUG] Querying Fog (${attempt + 1}/${retryCount + 1}): ${FOG_API_URL}/query (Timeout: ${currentTimeout}ms)`);
      const response = await axios.post(`${FOG_API_URL}/query`, payload, { 
        timeout: currentTimeout
      });

      if (response.data.status === 'not_found') {
        const error = new Error('Product not found') as any;
        error.response = { data: response.data };
        throw error;
      }
      
      const adapted = adaptBackendData(response.data);
      return {
        ...adapted,
        source: response.headers["x-cache"] === "HIT" ? "Fog Cache" : "Cloud Origin",
        latency: Date.now() - startTime
      };
    } catch (err: any) {
      if (err.message === 'Product not found') throw err;
      console.warn(`[WARN] Attempt ${attempt + 1} failed: ${err.message}`);
      
      if (attempt < retryCount) {
         await new Promise(resolve => setTimeout(resolve, 1000)); 
      }
    }
  }

  throw new Error(`無法連線至伺服器 (${FOG_API_URL})，請確認後端已開啟且手機與電腦連線至同一 WiFi。`);
}
