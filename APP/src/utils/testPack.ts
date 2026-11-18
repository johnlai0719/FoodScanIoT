/**
 * 測試集（測試包）的下載與讀取，供「逐案跑完測試集」的自動量測使用。
 *
 * 測試包由電腦端 `測試/量化測試/build_test_pack.py` 產生：一份 manifest.json
 * ＋所有**原始**照片（不先壓縮）。原始照片是刻意的——App 要自己用
 * utils/imageCompress.ts 壓，量到的壓縮耗時、上傳量才與真實使用相同。
 *
 * 存在 App 的 document 目錄（不是 cache）：cache 可能被系統清掉，
 * 跑到一半照片不見會讓那幾案變成錯誤，混進量測結果。
 */
import { Directory, File, Paths } from 'expo-file-system';

export interface PackImage {
  file: string;
  /** 原圖寬度，決定要不要縮（見 imageCompress.ts）。 */
  width: number;
}

export interface PackCase {
  case_id: string;
  category: string;
  set_version: string;
  images: PackImage[];
}

export interface PackManifest {
  /** 產生測試包時的凍結版本 tag，寫進量測紀錄才知道跑的是哪一版。 */
  pack_version: string;
  created: string;
  cases: PackCase[];
}

const DIR_NAME = 'testpack';
/**
 * 目前生效的測試包資料夾名稱記在這個檔案裡。下載先寫進新資料夾，**全部成功
 * 才改指標、刪舊的**——原本是先刪舊的再下載，伺服器沒開或下到一半斷線，
 * 手機上的測試包就沒了（2026-10-06 誤按兩次，都得整包 825 MB 重下）。
 */
const POINTER = 'testpack_current.txt';
const pointerFile = () => new File(Paths.document, POINTER);
const packDir = () => {
  try {
    const f = pointerFile();
    if (f.exists) {
      const name = f.textSync().trim();
      if (name) return new Directory(Paths.document, name);
    }
  } catch { /* 讀不到指標就退回舊的固定位置 */ }
  return new Directory(Paths.document, DIR_NAME);
};

export function imageUri(file: string): string {
  return new File(packDir(), file).uri;
}

/** 讀出已下載的測試包；不存在或照片不齊就回 null（不齊的測試包不能用）。 */
export function loadTestPack(): PackManifest | null {
  try {
    const m = new File(packDir(), 'manifest.json');
    if (!m.exists) return null;
    const manifest = JSON.parse(m.textSync()) as PackManifest;
    for (const c of manifest.cases) {
      for (const im of c.images) {
        if (!new File(packDir(), im.file).exists) return null;
      }
    }
    return manifest;
  } catch {
    return null;
  }
}

export interface DownloadProgress {
  done: number;
  total: number;
}

/**
 * 從電腦下載整個測試包。**先整個刪掉再下載**：新舊兩版混在一起的話，
 * 量測紀錄上寫的版本號就不可信了。
 */
export async function downloadTestPack(
  baseUrl: string,
  onProgress: (p: DownloadProgress) => void,
): Promise<PackManifest> {
  const base = baseUrl.trim().replace(/\/+$/, '');
  const old = packDir();
  const name = `${DIR_NAME}_${Date.now()}`;
  const dir = new Directory(Paths.document, name);
  dir.create({ intermediates: true });
  try {
    await File.downloadFileAsync(`${base}/manifest.json`, dir);
    const manifest = JSON.parse(new File(dir, 'manifest.json').textSync()) as PackManifest;
    const files = manifest.cases.flatMap(c => c.images.map(i => i.file));
    onProgress({ done: 0, total: files.length });
    for (let i = 0; i < files.length; i++) {
      await File.downloadFileAsync(`${base}/${encodeURIComponent(files[i])}`, dir);
      onProgress({ done: i + 1, total: files.length });
    }
    if (!files.every(f => new File(dir, f).exists)) {
      throw new Error('測試包下載不完整，請重新下載。');
    }
    // 新的完整了才切換；舊的此時才刪
    pointerFile().write(name);
    if (old.uri !== dir.uri && old.exists) {
      try { old.delete(); } catch { /* 刪不掉只是佔空間，不影響使用 */ }
    }
    return manifest;
  } catch (e) {
    // 失敗就丟掉這次的半成品，手機上原本的測試包保持不動
    try { if (dir.exists) dir.delete(); } catch { /* 同上 */ }
    throw e;
  }
}

export function deleteTestPack(): void {
  try {
    const dir = packDir();
    if (dir.exists) dir.delete();
  } catch { /* 刪不掉就算了，下次下載會先刪 */ }
}
