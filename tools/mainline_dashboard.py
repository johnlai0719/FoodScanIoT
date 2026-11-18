"""Local demo dashboard for the already-running FoodScanIoT mainline."""

import base64
import hmac
import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cloud"))
from telemetry import CLOUD_HEADER, DEMO_OCR_HEADER, REQUEST_ID_HEADER, parse_timing, safe_request_id

PORT = 8787
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ALLOWED_HOSTS.update(
    f"{host.strip().lower()}:{PORT}"
    for host in os.getenv("DASHBOARD_TAILSCALE_HOSTS", "").split(",")
    if host.strip()
)
CLOUD = os.getenv("DASHBOARD_CLOUD_URL", "http://100.119.217.100:3003").rstrip("/")
READER = os.getenv("DASHBOARD_READER_URL", "http://127.0.0.1:8180").rstrip("/")
FOG = os.getenv("DASHBOARD_FOG_URL", "http://100.86.249.39:3001").rstrip("/")
HTTP = build_opener(ProxyHandler({}))
SCAN = threading.Lock()
MAX_IMAGE = 6 * 1024 * 1024
MAX_ANALYSIS_BODY = 16 * 1024 * 1024
TRACES = {}
OCR_TRACES = {}
TRACE_LOCK = threading.Lock()
FOG_DEMO_LOCK = threading.Lock()
CLEANUP_LOCK = threading.Lock()
CLEARED_IDS = set()
TRACE_PHASES = ("started", "success", "degraded", "failed", "queued")
CACHE_STATES = {"HIT", "HIT-RAW", "MISS", "BYPASS", "DEGRADED", "STALE"}
TIMING_KEYS = {"total", "upstream", "localocr", "vision", "diagnosis", "r_ppocr",
               "r_vlcrop_hunyuan", "r_nutrition_structure", "r_qwen_fields", "r_assemble"}


def trace_duration(value):
    return value if type(value) is int and 0 <= value <= 600_000 else None


def trace_event(data):
    if not isinstance(data, dict):
        return None
    request_id = safe_request_id(data.get("request_id"))
    endpoint, phase = data.get("endpoint"), data.get("phase")
    if not request_id or endpoint not in ("fog", "cloud") or phase not in TRACE_PHASES:
        return None
    event = {"request_id": request_id, "endpoint": endpoint, "phase": phase}
    barcode = data.get("barcode")
    if isinstance(barcode, str) and re.fullmatch(r"TEST|[0-9]{8,13}", barcode):
        event["barcode"] = barcode
    elif request_id.startswith("demo-ocr-"):
        event["barcode"] = "TEST"
    if phase != "started":
        event["total_ms"] = trace_duration(data.get("total_ms"))
        status = data.get("http_status")
        event["http_status"] = status if type(status) is int and 100 <= status <= 599 else None
        cache = data.get("cache")
        event["cache"] = cache if isinstance(cache, str) and cache in CACHE_STATES else None
        for layer in ("fog_node", "fog_py", "cloud"):
            timing = data.get(layer)
            event[layer] = {
                key: timing[key] for key in TIMING_KEYS
                if isinstance(timing, dict) and trace_duration(timing.get(key)) is not None
            }
    return event


def health(base_url):
    try:
        with HTTP.open(base_url + "/health", timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError):
        return {"status": "unreachable"}


class Dashboard(BaseHTTPRequestHandler):
    def log_request(self, code="-", size="-"):
        if self.command != "GET" or self.path != "/api/live" or code != 200:
            super().log_request(code, size)

    def send_content(self, status, media_type, body):
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, data):
        self.send_content(status, "application/json; charset=utf-8",
                          json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def local_request(self):
        if self.headers.get("Host", "").lower() in ALLOWED_HOSTS:
            return True
        self.send_json(403, {"error": "請使用本機或已設定的 Tailscale 網址。"})
        return False

    def do_GET(self):
        if not self.local_request():
            return
        if self.path == "/":
            page = Path(__file__).with_suffix(".html").read_bytes()
            self.send_content(200, "text/html; charset=utf-8", page)
        elif self.path == "/api/status":
            self.send_json(200, {"cloud": health(CLOUD), "reader": health(READER),
                                 "fog": health(FOG)})
        elif self.path == "/api/demo/fog":
            with FOG_DEMO_LOCK:
                armed = time.monotonic() < getattr(self.server, "fog_demo_until", 0)
            self.send_json(200, {"armed": armed})
        elif self.path == "/api/demo/cleanup":
            with TRACE_LOCK:
                self.send_json(200, self.cleanup_scope())
        elif self.path == "/api/live":
            with TRACE_LOCK:
                latest = TRACES[next(reversed(TRACES))] if TRACES else None
                if latest:
                    latest = {**latest, "ocr_ready": latest["request_id"] in OCR_TRACES}
            if latest and latest["phase"] == "started":
                latest = {**latest, "stale": time.monotonic() - latest["received_at"] > 130}
            if latest:
                latest = {key: value for key, value in latest.items() if key != "received_at"}
            self.send_json(200, latest)
        elif self.path.startswith("/api/live/ocr/"):
            request_id = safe_request_id(self.path.removeprefix("/api/live/ocr/"))
            with TRACE_LOCK:
                entry = OCR_TRACES.get(request_id)
            self.send_json(200 if entry else 404, entry)
        else:
            self.send_json(404, {"error": "找不到這個頁面。"})

    def do_POST(self):
        if not self.local_request():
            return
        if self.path == "/api/cloud-backup/analyze":
            self.cloud_backup()
            return
        if self.path == "/api/demo/cleanup":
            self.cleanup_recent()
            return
        if self.path == "/api/live":
            self.receive_trace()
            return
        if self.path in ("/api/demo/fog/arm", "/api/demo/fog/cancel", "/api/demo/fog/claim"):
            self.fog_demo()
            return
        if self.path == "/api/live/ocr":
            self.receive_ocr()
            return
        if self.path != "/api/scan":
            self.send_json(404, {"error": "找不到這個端點。"})
            return
        if self.headers.get("Content-Type") != "application/octet-stream":
            self.send_json(415, {"error": "請從儀表板送出照片。"})
            return
        length = self.headers.get("Content-Length", "")
        if not length.isascii() or not length.isdecimal() or len(length) > 8:
            self.send_json(400, {"error": "缺少有效的圖片長度。"})
            return
        length = int(length)
        if not 0 < length <= MAX_IMAGE:
            self.send_json(413, {"error": "圖片須小於或等於 6 MiB。"})
            return
        if not SCAN.acquire(blocking=False):
            self.send_json(429, {"error": "上一張照片仍在辨識中。"})
            return
        try:
            self.connection.settimeout(15)
            image = self.rfile.read(length)
            if len(image) != length:
                self.send_json(400, {"error": "圖片上傳不完整。"})
                return
            self.scan(image)
        except TimeoutError:
            self.send_json(408, {"error": "圖片上傳逾時。"})
        finally:
            SCAN.release()

    def cleanup_scope(self):
        cutoff = time.monotonic() - 24 * 3600
        events = [event for event in TRACES.values() if event["received_at"] >= cutoff]
        return {
            "request_ids": sorted(event["request_id"] for event in events),
            "fog_keys": sorted({event["barcode"] for event in events
                                if event["endpoint"] == "fog" and event.get("barcode")}),
            "pending": any(event["phase"] == "queued" or
                           (event["phase"] == "started" and time.monotonic() - event["received_at"] < 130)
                           for event in events),
        }

    def cleanup_recent(self):
        origin = self.headers.get("Origin")
        if (self.headers.get("X-FoodScan-Cleanup") != "1"
                or self.headers.get("Sec-Fetch-Site") == "cross-site"
                or (origin and urlsplit(origin).netloc.lower() != self.headers.get("Host", "").lower())):
            self.send_json(403, {"error": "無法驗證清除操作。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16_384 or self.headers.get_content_type() != "application/json":
                raise ValueError
            self.connection.settimeout(5)
            approved = json.loads(self.rfile.read(length))
        except (ValueError, TimeoutError):
            self.send_json(400, {"error": "清除範圍格式有誤。"})
            return
        with CLEANUP_LOCK:
            with TRACE_LOCK:
                scope = self.cleanup_scope()
            if approved != scope or scope["pending"] or not scope["request_ids"]:
                self.send_json(409, {"error": "掃描仍在進行或範圍已變動，請重新確認。"})
                return
            try:
                request = Request(
                    CLOUD + "/api/demo/cleanup-uploads",
                    json.dumps(scope["request_ids"]).encode(),
                    {"Content-Type": "application/json", "X-API-Key": self.server.cloud_key},
                )
                with HTTP.open(request, timeout=8) as response:
                    cloud = json.load(response)
            except (OSError, ValueError):
                self.send_json(502, {"error": "Cloud 清除未確認，掃描清單已保留供重試。"})
                return
            deleted = 0
            failed = []
            for barcode in scope["fog_keys"]:
                try:
                    with HTTP.open(Request(f"{FOG}/cache/{barcode}", method="DELETE"), timeout=4) as response:
                        result = json.load(response)
                    if result.get("status") != "success":
                        raise ValueError("Fog cache deletion failed")
                    deleted += result["deleted"]
                except (OSError, ValueError, KeyError, TypeError):
                    failed.append(barcode)
            if failed:
                self.send_json(502, {"error": "Cloud 已清除。Fog 清除失敗的 key：" + "、".join(failed)})
                return
            with TRACE_LOCK:
                for rid in scope["request_ids"]:
                    TRACES.pop(rid, None)
                    OCR_TRACES.pop(rid, None)
                CLEARED_IDS.update(scope["request_ids"])
            self.send_json(200, {"scans": len(scope["request_ids"]), "cloud": cloud, "fog": deleted})

    def cloud_backup(self):
        if (self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site")
                or self.headers.get("X-FoodScan-Backup") != "1"):
            self.send_json(403, {"message": "請從 FoodScanIoT App 啟用備案。"})
            return
        length = self.headers.get("Content-Length", "")
        if (self.headers.get_content_type() != "application/json"
                or not length.isascii() or not length.isdecimal()
                or len(length) > 8 or not 0 < int(length) <= MAX_ANALYSIS_BODY):
            self.send_json(400, {"message": "分析資料格式有誤或照片總大小超過上限。"})
            return
        try:
            self.connection.settimeout(20)
            payload = json.loads(self.rfile.read(int(length)))
        except (ValueError, TimeoutError):
            payload = None
        if not isinstance(payload, dict):
            self.send_json(400, {"message": "無法讀取分析資料。"})
            return
        barcode = payload.get("barcode")
        images = payload.get("label_images", [])
        if (not isinstance(barcode, str) or not isinstance(images, list)
                or any(not isinstance(image, str) for image in images)):
            self.send_json(400, {"message": "條碼或照片格式有誤。"})
            return
        request_id = safe_request_id(self.headers.get(REQUEST_ID_HEADER))
        request = Request(
            CLOUD + "/api/analyze",
            json.dumps({"barcode": barcode, "label_images": images}).encode("utf-8"),
            {"Content-Type": "application/json", "X-API-Key": self.server.cloud_key,
             REQUEST_ID_HEADER: request_id},
        )
        try:
            with HTTP.open(request, timeout=110) as response:
                body = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if timing := response.headers.get(CLOUD_HEADER):
                    self.send_header(CLOUD_HEADER, timing)
                self.end_headers()
                self.wfile.write(body)
        except HTTPError as exc:
            exc.close()
            self.send_json(exc.code, {"message": f"雲端備案回傳 HTTP {exc.code}。"})
        except (URLError, TimeoutError):
            self.send_json(502, {"message": "雲端備案目前無法連線或分析逾時。"})

    def fog_demo(self):
        origin = self.headers.get("Origin")
        if (self.headers.get("Sec-Fetch-Site") == "cross-site"
                or (origin and urlsplit(origin).netloc.lower() != self.headers.get("Host", "").lower())
                or self.headers.get("X-FoodScan-Demo") != "1"):
            self.send_json(403, {"error": "無法驗證展示控制請求。"})
            return
        action = self.path.rsplit("/", 1)[-1]
        with FOG_DEMO_LOCK:
            if action == "arm":
                self.server.fog_demo_until = time.monotonic() + 120
                result = {"armed": True}
            elif action == "cancel":
                self.server.fog_demo_until = 0
                result = {"armed": False}
            else:
                claimed = time.monotonic() < getattr(self.server, "fog_demo_until", 0)
                self.server.fog_demo_until = 0
                result = {"local_only": claimed}
        self.send_json(200, result)

    def receive_ocr(self):
        if not hmac.compare_digest(self.headers.get("X-API-Key", "").encode(),
                                   self.server.cloud_key.encode()):
            self.send_json(401, {"error": "無法驗證 Cloud 回報。"})
            return
        length = self.headers.get("Content-Length", "")
        if (self.headers.get_content_type() != "application/json"
                or not length.isascii() or not length.isdecimal()
                or len(length) > 7 or not 0 < int(length) <= 7_000_000):
            self.send_json(400, {"error": "無效辨識紀錄。"})
            return
        try:
            self.connection.settimeout(5)
            event = json.loads(self.rfile.read(int(length)))
        except (ValueError, TimeoutError):
            event = None
        request_id = safe_request_id(event.get("request_id")) if isinstance(event, dict) else ""
        if (not request_id.startswith("demo-ocr-") or not isinstance(event.get("trace"), dict)
                or not isinstance(event.get("image"), str)):
            self.send_json(400, {"error": "無效辨識紀錄。"})
            return
        with TRACE_LOCK:
            if request_id in CLEARED_IDS:
                self.send_json(200, {"ok": True})
                return
            OCR_TRACES[request_id] = {"image": event["image"], "trace": event["trace"]}
            if len(OCR_TRACES) > 3:
                del OCR_TRACES[next(iter(OCR_TRACES))]
        self.send_json(200, {"ok": True})

    def receive_trace(self):
        length = self.headers.get("Content-Length", "")
        if (self.headers.get_content_type() != "application/json" or not length.isascii()
                or not length.isdecimal() or len(length) > 4 or not 0 < int(length) <= 4096):
            self.send_json(400, {"error": "無效事件。"})
            return
        try:
            self.connection.settimeout(5)
            event = trace_event(json.loads(self.rfile.read(int(length))))
        except (ValueError, TimeoutError):
            event = None
        if event is None:
            self.send_json(400, {"error": "無效事件。"})
            return
        request_id = event["request_id"]
        with TRACE_LOCK:
            if request_id in CLEARED_IDS:
                self.send_json(200, {"ok": True})
                return
            previous = TRACES.get(request_id)
            # A delayed start must not overwrite a completed request.
            if previous is None or previous["phase"] == "started" or (
                previous["phase"] == "queued" and event["phase"] in ("success", "degraded", "failed")
            ):
                event["received_at"] = time.monotonic()
                TRACES[request_id] = event
                if len(TRACES) > 100:
                    del TRACES[next(iter(TRACES))]
        self.send_json(200, {"ok": True})

    def scan(self, image):
        request_id = uuid.uuid4().hex
        event = {"request_id": request_id, "endpoint": "cloud", "barcode": "TEST",
                 "received_at": time.monotonic(), "phase": "started"}
        with TRACE_LOCK:
            TRACES[request_id] = event
            if len(TRACES) > 100:
                del TRACES[next(iter(TRACES))]
        payload = {"barcode": "TEST", "label_images": [base64.b64encode(image).decode("ascii")]}
        request = Request(CLOUD + "/api/analyze", json.dumps(payload).encode("utf-8"),
                          {"Content-Type": "application/json", "X-API-Key": self.server.cloud_key,
                           REQUEST_ID_HEADER: request_id, DEMO_OCR_HEADER: "1"})
        started = time.monotonic()
        try:
            with HTTP.open(request, timeout=135) as response:
                result = json.load(response)
                timing = parse_timing(response.headers.get(CLOUD_HEADER))
        except HTTPError as exc:
            with TRACE_LOCK:
                event["phase"] = "failed"
            with exc:
                try:
                    detail = json.load(exc).get("detail")
                except ValueError:
                    detail = None
            self.send_json(502, {"error": detail if isinstance(detail, str) else
                                 f"Cloud 回傳 HTTP {exc.code}。"})
            return
        except (URLError, OSError, ValueError):
            with TRACE_LOCK:
                event["phase"] = "failed"
            self.send_json(502, {"error": "Cloud 無法連線、回應逾時或回傳無效資料。"})
            return
        with TRACE_LOCK:
            event.update(phase={"success": "success", "degraded": "degraded"}.get(result.get("status"), "failed"),
                         cloud=timing, http_status=200)
        self.send_json(200, {"request_id": request_id, "timing": timing,
                             "elapsed_ms": round((time.monotonic() - started) * 1000),
                             "demo_ocr": result.pop("demo_ocr", None),
                             "result": result})


def main():
    key = os.getenv("API_SHARED_SECRET", "").strip()
    if not key:
        config = ROOT / "cloud" / ".env"
        if config.is_file():
            key = next((line.partition("=")[2].strip() for line in
                        config.read_text(encoding="utf-8-sig").splitlines()
                        if line.startswith("API_SHARED_SECRET=")), "")
    if not key:
        raise SystemExit("API_SHARED_SECRET must be set in the environment or cloud/.env.")
    with ThreadingHTTPServer(("127.0.0.1", PORT), Dashboard) as server:
        server.cloud_key = key
        print(f"Mainline dashboard: http://127.0.0.1:{PORT}/", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
