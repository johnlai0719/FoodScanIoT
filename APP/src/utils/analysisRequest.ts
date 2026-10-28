/** Fog's existing protocol uses TEST for a photo-only request, outside shared barcode caching. */
export function buildAnalysisRequest(barcodeInput: string, images: string[]) {
  const barcode = barcodeInput.trim() || (images.length ? 'TEST' : '');
  if (!barcode) throw new Error('請先掃描條碼或拍攝標示照片。');
  if (barcode !== 'TEST' && !/^\d{8,13}$/.test(barcode)) {
    throw new Error('條碼需為 8–13 位數字；若只有標示照片，請清空條碼後送出。');
  }
  if (barcode === 'TEST' && !images.length) throw new Error('純照片分析需要先拍攝或選取標示照片。');
  return { barcode, label_images: images };
}
