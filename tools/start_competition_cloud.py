"""Start the existing competition database, Vision reader and Cloud API.

Secrets stay in child environments, never in arguments or launcher output.
Existing services are checked and reused, never killed or replaced.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.artifacts'
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def docker_info(name):
    result = subprocess.run(['docker', 'inspect', name], capture_output=True,
                            text=True, encoding='utf-8', creationflags=FLAGS)
    if result.returncode:
        raise RuntimeError(f'Docker container unavailable: {name}. Start Docker Desktop and check the existing container.')
    return json.loads(result.stdout)[0]


def container_env(info):
    return dict(item.split('=', 1) for item in info['Config']['Env'] if '=' in item)


def health(host, port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://{host}:{port}/health', timeout=3) as response:
            return json.load(response)
    except Exception:
        return None


def start_service(name, command, cwd, env, host, port, validate, processes):
    current = health(host, port)
    if current is not None:
        if not validate(current):
            raise RuntimeError(f'{name}: port {port} responds with an unexpected or unhealthy configuration; existing service was left unchanged.')
        print(f'[OK] {name} already running ({port})', flush=True)
        return
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex((host, port)) == 0:
            raise RuntimeError(f'{name}: port {port} is occupied; existing process was left unchanged.')
    with (OUT / f'{name}.stdout.log').open('ab') as stdout, (OUT / f'{name}.stderr.log').open('ab') as stderr:
        child = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout,
                                 stderr=stderr, creationflags=FLAGS)
    processes[name] = child.pid
    (OUT / 'competition-services.json').write_text(json.dumps(processes, indent=2), encoding='utf-8')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f'{name} exited. Check .artifacts/{name}.stderr.log locally.')
        result = health(host, port)
        if result is not None:
            if not validate(result):
                raise RuntimeError(f'{name} started but its health configuration is not ready. Check its local log.')
            print(f'[OK] {name} ready ({port})', flush=True)
            return
        time.sleep(1)
    raise RuntimeError(f'{name} has not become healthy within 60 seconds. Check its local log before retrying.')


def main():
    OUT.mkdir(exist_ok=True)
    db_name = os.getenv('COMPETITION_DB_CONTAINER', 'app-sync-server-db-1')
    db_info = docker_info(db_name)
    if not db_info['State']['Running']:
        result = subprocess.run(['docker', 'start', db_name], capture_output=True, creationflags=FLAGS)
        if result.returncode:
            raise RuntimeError('Could not start the existing database container.')
    db = container_env(db_info)
    # Keep compatibility with the existing local setup. Explicit process env wins.
    env = {}
    for file in [ROOT.parents[1] / '.env', ROOT.parents[1] / 'APP-sync-server/server/.env',
                 ROOT / '.env', ROOT / 'server/.env']:
        if file.is_file():
            env.update({k: v for k, v in dotenv_values(file).items() if v is not None})
    # Old local deployment keeps shared credentials in its stopped Cloud container.
    try:
        previous = container_env(docker_info('app-sync-server-cloud-server-1'))
        for key in ['GEMINI_API_KEY', 'API_SHARED_SECRET', 'JWT_SECRET']:
            if previous.get(key):
                env[key] = previous[key]
    except RuntimeError:
        pass
    env.update(os.environ)
    env.update(DB_HOST='127.0.0.1', DB_PORT='5432', DB_NAME=db.get('POSTGRES_DB', 'product_db'),
               DB_USER=db.get('POSTGRES_USER', 'postgres'), DB_PASSWORD=db.get('POSTGRES_PASSWORD', ''),
               VISION_BACKEND='google_vision_rules_gemini', READER_URL='http://127.0.0.1:8181',
               READER_HOST='127.0.0.1', READER_PORT='8181', PYTHONUTF8='1')
    env.setdefault('PIPELINE_DASHBOARD_URL', 'http://127.0.0.1:8787')
    env.pop('DATABASE_URL', None)
    credentials = env.get('GOOGLE_APPLICATION_CREDENTIALS')
    if not credentials:
        candidates = list((ROOT.parents[1] / 'GoogleCloudVision').glob('*.json'))
        if len(candidates) == 1:
            credentials = str(candidates[0])
    if not credentials or not Path(credentials).is_file() or not env.get('GEMINI_API_KEY'):
        raise RuntimeError('Vision credential file or GEMINI_API_KEY is missing. Check the existing local environment settings.')
    env['GOOGLE_APPLICATION_CREDENTIALS'] = str(Path(credentials).resolve())
    import psycopg2
    ready = False
    for _ in range(20):
        try:
            with psycopg2.connect(host=env['DB_HOST'], port=env['DB_PORT'], dbname=env['DB_NAME'],
                                  user=env['DB_USER'], password=env['DB_PASSWORD'], connect_timeout=2) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                    tables = {row[0] for row in cursor.fetchall()}
            ready = True
            break
        except psycopg2.Error:
            time.sleep(1)
    if not ready:
        raise RuntimeError('Database connection failed; existing data was left unchanged.')
    if not {'products', 'additives'}.issubset(tables):
        raise RuntimeError('The database does not contain the required products/additives tables. No tables were created or changed.')
    print('[OK] Existing database ready (5432)', flush=True)
    host = os.getenv('COMPETITION_CLOUD_HOST')
    if not host:
        exe = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Tailscale/tailscale.exe'
        try:
            result = subprocess.run([str(exe), 'ip', '-4'], capture_output=True, text=True, timeout=5, creationflags=FLAGS)
            host = result.stdout.strip().splitlines()[0] if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, IndexError):
            host = None
    if not host:
        host = '127.0.0.1'
        print('[NOTE] Tailscale unavailable: Cloud will only be reachable on this computer.', flush=True)
    state = OUT / 'competition-services.json'
    try:
        processes = json.loads(state.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        processes = {}
    start_service('vision-reader', [sys.executable, 'tools/reader_vision_competition.py'], ROOT, env,
                  '127.0.0.1', 8181, lambda h: h.get('routing_policy') == 'vision_text_envelope_v1'
                  and h.get('ocr_quality_policy') == 'vision_min_text_v1'
                  and h.get('credentials_configured') and h.get('gemini_configured'), processes)
    start_service('cloud', [sys.executable, '-m', 'uvicorn', 'main:app', '--host', host, '--port', '3003'],
                  ROOT / 'server', env, host, 3003,
                  lambda h: h.get('layer') == 'cloud' and h.get('database') == 'ok'
                  and h.get('vision_backend') == 'google_vision_rules_gemini', processes)
    print(f'\nReady: http://{host}:3003/health\nLogs: {OUT}\nApp and dashboard are started separately.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not print dependency exception messages: they can contain credentials.
        print(f'[FAILED] {exc}' if isinstance(exc, RuntimeError) else f'[FAILED] {type(exc).__name__}; check local configuration.', file=sys.stderr)
        sys.exit(1)
