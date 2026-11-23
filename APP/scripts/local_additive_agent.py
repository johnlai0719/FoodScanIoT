import argparse
import json
import os
import re
import subprocess
import unicodedata
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


# Summaries of the functional classes listed in the Codex GSFA entries.
ADDITIVES = (
    {"name": "果膠", "aliases": ("果膠", "果胶", "pectin"), "ins": "440",
     "evidence": "果膠可作為增稠劑與凝膠劑，用來增加食品的黏稠度或讓食品形成凝膠。",
     "source": "https://www.fao.org/gsfaonline/additives/details.html?id=21"},
    {"name": "檸檬酸", "aliases": ("檸檬酸", "柠檬酸", "枸櫞酸", "citric acid"), "ins": "330",
     "evidence": "檸檬酸可作為酸度調節劑，用來調整食品的酸度。",
     "source": "https://www.fao.org/gsfaonline/additives/details.html?id=173"},
)


def find_additives(question):
    query = unicodedata.normalize("NFKC", question).casefold()
    return [
        item for item in ADDITIVES
        if any(alias in query for alias in item["aliases"])
        or re.search(rf"(?<![a-z0-9])(?:ins|e)[ -]*{item['ins']}(?![a-z0-9])", query)
    ]


def answer(question, executable, model):
    matches = find_additives(question)
    if not matches:
        return {"answer": "本機知識包目前找不到這個成分的用途說明。可以試問果膠或檸檬酸。"}
    if len(matches) > 1:
        return {"answer": "找到多個成分，請一次問一種。"}
    item = matches[0]
    prompt = (
        "用一句自然的繁體中文改寫以下用途摘要，保留成分名稱和用途。"
        "只輸出該句。不要新增資料、例子、健康宣稱、安全判斷或網址。\n"
        f"用途摘要：{item['evidence']}"
    )
    result = subprocess.run(
        [str(executable), "run", str(model), "--backend", "cpu", "--thinking", "false",
         "--temperature", "0", "--max-num-tokens", "2048", "--prompt", prompt],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"}, timeout=20, check=True,
    )
    explanation = result.stdout.strip()
    if not explanation or len(explanation) > 240 or re.search(r"安全|無害|健康|過敏|劑量|https?://", explanation):
        explanation = item["evidence"]
    return {"answer": explanation,
            "additive": {"name": item["name"], "ins": item["ins"], "summary": item["evidence"]},
            "source": {"title": f"Codex GSFA：{item['name']}（INS {item['ins']}）",
                                               "url": item["source"]}}


def main():
    parser = argparse.ArgumentParser(description="Run the PackMeow additive Q&A preview on loopback.")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--litert", type=Path, required=True)
    args = parser.parse_args()
    if not args.model.is_file() or not args.litert.is_file():
        parser.error("Both --model and --litert must point to existing files.")
    origins = {"http://localhost:8091", "http://127.0.0.1:8091"}

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body):
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            if self.headers.get("Origin") in origins:
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_OPTIONS(self):
            self.reply(200, {})

        def do_POST(self):
            if self.path != "/ask" or self.headers.get("Origin") not in origins | {None}:
                self.reply(403, {})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError()
                body = json.loads(self.rfile.read(length))
                question = body.get("question") if isinstance(body, dict) else None
                if not isinstance(question, str) or not 0 < len(question.strip()) <= 120:
                    raise ValueError()
                if re.search(r"安全|有毒|過敏|劑量|吃多少|能不能吃|可以吃嗎|有害|致癌", question):
                    self.reply(200, {"answer": "目前可以解釋添加物的一般加工用途。食品過敏資訊請查看包裝標示。"})
                    return
                self.reply(200, answer(question, args.litert, args.model))
            except (ValueError, UnicodeError):
                self.reply(400, {})
            except (OSError, subprocess.SubprocessError):
                self.reply(503, {})

    server = HTTPServer(("127.0.0.1", 8092), Handler)
    print("PackMeow local additive agent on http://127.0.0.1:8092", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
