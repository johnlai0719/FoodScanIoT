"""Detect ALL dataset photos without loading recognition weights."""
import argparse
import json
import time
from pathlib import Path
from easyocr_baseline import resolve_image


def main():
    import easyocr
    import torch
    import numpy as np
    from PIL import Image
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--model-dir',type=Path,default=Path('/opt/easyocr-models'))
    parser.add_argument('--download',action='store_true')
    parser.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='auto')
    parser.add_argument('--limit', type=int, default=0, help='0 runs all cases')
    parser.add_argument('--canvas-size', type=int, default=2400)
    args = parser.parse_args()
    if args.limit < 0 or args.canvas_size <= 0:
        parser.error('limit must be nonnegative and canvas-size must be positive')
    cuda = torch.cuda.is_available()
    if args.device == 'cuda' and not cuda:
        raise RuntimeError('CUDA requested but unavailable; refusing silent CPU fallback')
    use_gpu = cuda if args.device == 'auto' else args.device == 'cuda'
    device = 'cuda' if use_gpu else 'cpu'
    init_started = time.perf_counter()
    reader = easyocr.Reader(['ch_tra','en'], gpu=use_gpu, recognizer=False,
        model_storage_directory=str(args.model_dir), download_enabled=args.download, verbose=False)
    if use_gpu and not str(reader.device).startswith('cuda'):
        raise RuntimeError('Reader did not select CUDA')
    init_s = time.perf_counter() - init_started
    cases = json.loads((args.dataset/'cases.json').read_text(encoding='utf-8'))['cases']
    if args.limit:
        cases = cases[:args.limit]
    args.output.mkdir(parents=True,exist_ok=True)
    count = failed = 0
    for case in cases:
        dest = args.output/(case['case_id']+'.json')
        if dest.exists():
            old = json.loads(dest.read_text(encoding='utf-8'))
            if old.get('status') == 'completed':
                if old.get('device', 'cpu') != device or old.get('canvas_size') != args.canvas_size:
                    raise RuntimeError('Existing output uses a different device/config; use a new output directory')
                count+=1; continue
        record = {'case_id':case['case_id'],'category':case['category'],'images':[],
                  'engine':'easyocr','version':easyocr.__version__,'recognition':False,
                  'canvas_size':args.canvas_size,'device':device,'torch_version':torch.__version__}
        try:
            for relative in case['images']:
                source = resolve_image(args.dataset/'images',relative)
                if use_gpu:
                    torch.cuda.synchronize()
                started = time.perf_counter()
                pixels = np.array(Image.open(source).convert('RGB'))
                horizontal, free = reader.detect(pixels,canvas_size=args.canvas_size,mag_ratio=1.0)
                if use_gpu:
                    torch.cuda.synchronize()
                boxes = [[[int(x0),int(y0)],[int(x1),int(y0)],[int(x1),int(y1)],[int(x0),int(y1)]]
                         for x0,x1,y0,y1 in horizontal[0]]
                boxes += [[[int(x),int(y)] for x,y in polygon] for polygon in free[0]]
                record['images'].append({'source':str(source),'path':relative,
                    'lines':[{'box':box} for box in boxes], 'elapsed_s':round(time.perf_counter()-started,3)})
            record['status']='completed';count+=1
        except Exception as exc:
            record['status']='failed';record['error']={'type':type(exc).__name__};failed+=1
        dest.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'case':case['case_id'],'completed':count,'failed':failed}),flush=True)
    summary = {'requested':len(cases),'completed':count,'failed':failed,
               'images':sum(len(case['images']) for case in cases),'recognition':False,
               'device':device,'canvas_size':args.canvas_size,'reader_init_s':round(init_s,3),
               'torch_version':torch.__version__,'cuda_version':torch.version.cuda,
               'gpu_name':torch.cuda.get_device_name(0) if use_gpu else None}
    summary_name = f'_summary-limit{args.limit}.json' if args.limit else '_summary.json'
    (args.output/summary_name).write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)
    if failed: raise SystemExit(1)


if __name__ == '__main__': main()
