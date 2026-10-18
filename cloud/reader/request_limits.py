"""ASGI admission before JSON parsing: bound active+waiting bodies."""
import json
import threading


class RequestLimits:
    def __init__(self, app, max_bytes=16 * 1024**2, capacity=2):
        self.app = app
        self.max_bytes = max_bytes
        self.slots = threading.BoundedSemaphore(capacity)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') != '/read' or scope.get('method') != 'POST':
            return await self.app(scope, receive, send)
        async def reject(status, detail):
            body = json.dumps({'detail': detail}, ensure_ascii=False).encode()
            headers = [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())]
            if status == 503:
                headers.append((b'retry-after', b'3'))
            await send({'type': 'http.response.start', 'status': status, 'headers': headers})
            await send({'type': 'http.response.body', 'body': body})
        if not self.slots.acquire(blocking=False):
            return await reject(503, 'reader 忙碌，請稍後重試')
        try:
            parts, size = [], 0
            for name, value in scope.get('headers', []):
                if name.lower() == b'content-length':
                    try:
                        length = int(value)
                    except ValueError:
                        return await reject(400, 'Content-Length 無效')
                    if length < 0:
                        return await reject(400, 'Content-Length 無效')
                    if length > self.max_bytes:
                        return await reject(413, '請求內容過大')
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                size += len(chunk)
                if size > self.max_bytes:
                    return await reject(413, '請求內容過大')
                parts.append(chunk)
                if not message.get('more_body', False):
                    break
            body = b''.join(parts)
            parts.clear()
            delivered = False
            async def bounded_receive():
                nonlocal delivered, body
                if not delivered:
                    delivered = True
                    result = {'type': 'http.request', 'body': body, 'more_body': False}
                    body = b''
                    return result
                return await receive()
            await self.app(scope, bounded_receive, send)
        finally:
            self.slots.release()
