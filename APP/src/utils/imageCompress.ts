/**
 * 上傳前的影像壓縮。拍照、選相簿、測試集自動量測三條路都走這一支。
 *
 * 為什麼抽出來：測試集量測必須用**與真實使用完全相同**的壓縮，量到的上傳量
 * 與辨識結果才代表使用者實際的情況；原本這段寫在 PackageImageScanner 裡面，
 * 測試集要用就得複製一份，兩份遲早分岔。
 */
import * as ImageManipulator from 'expo-image-manipulator';

// 依據實驗 05（05-壓縮對本地管線之影響），若壓至 1280px 會使本地 vlcrop 管線添加物 F1 重挫 9.7 點；
// 提升至最長邊 1920px (JPEG quality 0.85) 則可通過統計非劣性驗證（差距僅 -1.6 點，CI 跨 0），
// 橫向拍攝即對應 1920x1080 (FHD)，直向拍攝寬度保留 1920px 以維護橫排小字辨識率。
export const MAX_WIDTH = 1920;
export const QUALITY = 0.85;

export interface Compressed {
  base64: string;
  width: number;
  /** 壓縮耗時（毫秒，手機自己的時鐘）。五段延遲的第一段。 */
  ms: number;
}

/** `srcWidth` 是原圖寬度，用來決定要不要縮。回傳 null 表示壓縮失敗。 */
export async function compressImage(uri: string, srcWidth: number): Promise<Compressed | null> {
  const t0 = Date.now();
  // 只縮不放。resize 設的是**結果尺寸**而非上限，寬度本來就小於 1920 的圖
  // （截圖、網路存下的小圖）若照樣傳進去會被放大：檔案變大，細節一點沒多。
  const actions = srcWidth > MAX_WIDTH ? [{ resize: { width: MAX_WIDTH } }] : [];
  const out = await ImageManipulator.manipulateAsync(uri, actions, {
    compress: QUALITY,
    format: ImageManipulator.SaveFormat.JPEG,
    base64: true,
  });
  if (!out.base64) return null;
  // 實機驗收用：確認送出去的確實是這組參數壓過的結果。
  console.log(`[compress] ${srcWidth}px → ${out.width}px, `
    + `base64 ${(out.base64.length / 1024).toFixed(0)}KB`);
  return { base64: out.base64, width: out.width, ms: Date.now() - t0 };
}
