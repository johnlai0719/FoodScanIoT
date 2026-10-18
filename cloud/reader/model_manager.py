"""Own local llama processes; never adopt/terminate an unrelated listener."""
from contextlib import contextmanager
import threading

import start_models


class ModelUnavailable(RuntimeError):
    pass


class ModelManager:
    def __init__(self, configs=None):
        self.configs = configs if configs is not None else start_models.SERVERS
        self.processes = {}
        self._lock = threading.RLock()
        self._closed = False
        self.state = "not_started"
        self.detail = ""

    def status(self):
        return {"state": self.state, "detail": self.detail,
                "owned_pids": {name: p.pid for name, p in list(self.processes.items())
                               if p.poll() is None}}

    def ensure_ready(self):
        with self._lock:
            if self._closed:
                raise ModelUnavailable("模型管理器已關閉")
            created = []
            try:
                for config in self.configs:
                    name = config["name"]
                    p = self.processes.get(name)
                    if p is not None and p.poll() is None:
                        if not start_models.alive(config["port"]):
                            raise ModelUnavailable("%s 尚未就緒" % name)
                        continue
                    if start_models.alive(config["port"]):
                        raise ModelUnavailable(
                            "%s 埠已有外部模型服務；請先停止原啟動器，讓 reader 自行啟動模型" % name)
                    self.state = "loading"
                    p = start_models.start(config)
                    if p is None:
                        raise ModelUnavailable("%s 埠已由其他服務占用" % name)
                    self.processes[name] = p
                    created.append(name)
                self.state, self.detail = "ready", ""
            except Exception as exc:
                for name in reversed(created):
                    try:
                        start_models.stop(self.processes[name])
                    except Exception:
                        pass
                self.state, self.detail = "failed", str(exc)
                raise ModelUnavailable(str(exc)) from exc

    @contextmanager
    def pause_hunyuan(self):
        with self._lock:
            self.ensure_ready()
            self.state = "pausing"
            try:
                start_models.stop(self.processes["hunyuan"])
            except Exception as exc:
                self.state, self.detail = "failed", str(exc)
                raise ModelUnavailable("Hunyuan 卸載失敗，未執行備援") from exc
            self.state = "paused"
            try:
                yield
            finally:
                # Failure leaves state=failed; the next request can retry ensure_ready.
                self.ensure_ready()

    def close(self):
        with self._lock:
            self._closed = True
            errors = []
            for name, p in reversed(list(self.processes.items())):
                try:
                    start_models.stop(p)
                except Exception as exc:
                    errors.append("%s: %s" % (name, exc))
            self.state = "closed" if not errors else "failed"
            self.detail = "; ".join(errors)
