import { buildAnalysisRequest } from '../analysisRequest';

test('photo-only requests use the existing TEST protocol and keep every photo', () => {
  expect(buildAnalysisRequest('  ', ['photo-one', 'photo-two']))
    .toEqual({ barcode: 'TEST', label_images: ['photo-one', 'photo-two'] });
});
test('normal barcode lookup and barcode-plus-photo keep the real barcode', () => {
  expect(buildAnalysisRequest(' 4710018123456 ', [])).toEqual({ barcode: '4710018123456', label_images: [] });
  expect(buildAnalysisRequest('12345678', ['photo']).barcode).toBe('12345678');
});
test.each(['123', 'DEMO-DRINK', '47100181234567', '4710abc12345'])('invalid barcode %s is rejected before sending', barcode => {
  expect(() => buildAnalysisRequest(barcode, ['photo'])).toThrow('8–13');
});
test('neither empty input nor a photo-only marker without photos is submitted', () => {
  expect(() => buildAnalysisRequest('', [])).toThrow('請先');
  expect(() => buildAnalysisRequest('TEST', [])).toThrow('需要先拍攝');
});
