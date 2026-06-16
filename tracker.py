import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Dict, List, Optional
import hashlib


class TrackerState:
    def __init__(self):
        self.torrents: Dict[bytes, dict] = {}
        self.lock = threading.Lock()

    def announce(self, info_hash: bytes, peer_id: bytes, ip: str, port: int,
                 event: str = "started", uploaded: int = 0, downloaded: int = 0,
                 left: int = 0) -> dict:
        with self.lock:
            if info_hash not in self.torrents:
                self.torrents[info_hash] = {
                    'peers': {},
                    'complete': 0,
                    'incomplete': 0,
                    'created': time.time()
                }

            torrent = self.torrents[info_hash]
            peer_key = peer_id

            is_seed = (left == 0)

            if event == "stopped":
                if peer_key in torrent['peers']:
                    old_left = torrent['peers'][peer_key]['left']
                    if old_left == 0:
                        torrent['complete'] -= 1
                    else:
                        torrent['incomplete'] -= 1
                    del torrent['peers'][peer_key]
            else:
                if peer_key not in torrent['peers']:
                    if is_seed:
                        torrent['complete'] += 1
                    else:
                        torrent['incomplete'] += 1
                else:
                    old_left = torrent['peers'][peer_key]['left']
                    if old_left == 0:
                        torrent['complete'] -= 1
                    else:
                        torrent['incomplete'] -= 1
                    if is_seed:
                        torrent['complete'] += 1
                    else:
                        torrent['incomplete'] += 1

                torrent['peers'][peer_key] = {
                    'peer_id': peer_id,
                    'ip': ip,
                    'port': port,
                    'uploaded': uploaded,
                    'downloaded': downloaded,
                    'left': left,
                    'last_seen': time.time(),
                    'is_seed': is_seed
                }

            peer_list = []
            for pid, info in torrent['peers'].items():
                if pid != peer_key:
                    peer_list.append({
                        'peer_id': pid.hex() if isinstance(pid, bytes) else str(pid),
                        'ip': info['ip'],
                        'port': info['port']
                    })

            return {
                'interval': 30,
                'complete': torrent['complete'],
                'incomplete': torrent['incomplete'],
                'peers': peer_list
            }

    def get_torrent_info(self, info_hash: bytes) -> Optional[dict]:
        with self.lock:
            if info_hash in self.torrents:
                t = self.torrents[info_hash]
                return {
                    'info_hash': info_hash.hex(),
                    'complete': t['complete'],
                    'incomplete': t['incomplete'],
                    'num_peers': len(t['peers']),
                    'peers': [
                        {'ip': p['ip'], 'port': p['port'], 'is_seed': p['is_seed']}
                        for p in t['peers'].values()
                    ]
                }
            return None

    def list_torrents(self) -> List[dict]:
        with self.lock:
            result = []
            for ih, t in self.torrents.items():
                result.append({
                    'info_hash': ih.hex(),
                    'complete': t['complete'],
                    'incomplete': t['incomplete'],
                    'num_peers': len(t['peers'])
                })
            return result

    def cleanup_stale_peers(self, timeout: int = 120):
        with self.lock:
            now = time.time()
            for ih in list(self.torrents.keys()):
                torrent = self.torrents[ih]
                stale_peers = []
                for pid, info in torrent['peers'].items():
                    if now - info['last_seen'] > timeout:
                        stale_peers.append(pid)

                for pid in stale_peers:
                    old_left = torrent['peers'][pid]['left']
                    if old_left == 0:
                        torrent['complete'] -= 1
                    else:
                        torrent['incomplete'] -= 1
                    del torrent['peers'][pid]


class TrackerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == '/announce':
            self._handle_announce(params)
        elif path == '/scrape':
            self._handle_scrape(params)
        elif path == '/status':
            self._handle_status()
        else:
            self.send_error(404, "Not Found")

    def _handle_announce(self, params: dict):
        state: TrackerState = self.server.tracker_state

        info_hash_hex = params.get('info_hash', [None])[0]
        peer_id_hex = params.get('peer_id', [None])[0]
        port_str = params.get('port', [None])[0]
        event = params.get('event', ['started'])[0]
        uploaded = int(params.get('uploaded', ['0'])[0])
        downloaded = int(params.get('downloaded', ['0'])[0])
        left = int(params.get('left', ['0'])[0])

        if not info_hash_hex or not peer_id_hex or not port_str:
            self.send_error(400, "Missing required parameters")
            return

        try:
            info_hash = bytes.fromhex(info_hash_hex)
            peer_id = bytes.fromhex(peer_id_hex)
            port = int(port_str)
        except (ValueError, TypeError):
            self.send_error(400, "Invalid parameters")
            return

        ip = self.client_address[0]
        if 'ip' in params:
            ip = params['ip'][0]

        result = state.announce(
            info_hash=info_hash,
            peer_id=peer_id,
            ip=ip,
            port=port,
            event=event,
            uploaded=uploaded,
            downloaded=downloaded,
            left=left
        )

        self.send_json(result)

    def _handle_scrape(self, params: dict):
        state: TrackerState = self.server.tracker_state
        info_hash_hex = params.get('info_hash', [None])[0]

        if info_hash_hex:
            try:
                info_hash = bytes.fromhex(info_hash_hex)
            except ValueError:
                self.send_error(400, "Invalid info_hash")
                return
            info = state.get_torrent_info(info_hash)
            if info:
                self.send_json({'files': {info['info_hash']: info}})
            else:
                self.send_json({'files': {}})
        else:
            torrents = state.list_torrents()
            self.send_json({'files': torrents})

    def _handle_status(self):
        state: TrackerState = self.server.tracker_state
        torrents = state.list_torrents()
        self.send_json({
            'total_torrents': len(torrents),
            'torrents': torrents
        })

    def send_json(self, data: dict):
        body = json.dumps(data, indent=2).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class TrackerServer:
    def __init__(self, host: str = '0.0.0.0', port: int = 8080):
        self.host = host
        self.port = port
        self.state = TrackerState()
        self.server = None
        self._thread = None

    def start(self, daemon: bool = True):
        self.server = HTTPServer((self.host, self.port), TrackerHandler)
        self.server.tracker_state = self.state

        if daemon:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            self._run()

    def _run(self):
        print(f"Tracker server started on {self.host}:{self.port}")
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            pass
        print("Tracker server stopped")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server = None

    def get_announce_url(self) -> str:
        return f"http://{self.host}:{self.port}/announce"


class TrackerClient:
    def __init__(self, tracker_url: str):
        self.tracker_url = tracker_url

    def announce(self, info_hash: bytes, peer_id: bytes, port: int,
                 event: str = "started", uploaded: int = 0,
                 downloaded: int = 0, left: int = 0) -> dict:
        import urllib.request
        import urllib.parse

        params = {
            'info_hash': info_hash.hex(),
            'peer_id': peer_id.hex(),
            'port': port,
            'event': event,
            'uploaded': uploaded,
            'downloaded': downloaded,
            'left': left
        }

        url = self.tracker_url + '?' + urllib.parse.urlencode(params)

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data
        except Exception as e:
            print(f"Tracker announce failed: {e}")
            return {'peers': [], 'interval': 30, 'complete': 0, 'incomplete': 0}

    def scrape(self, info_hash: bytes = None) -> dict:
        import urllib.request
        import urllib.parse

        base = self.tracker_url.replace('/announce', '/scrape')
        if info_hash:
            url = base + '?info_hash=' + info_hash.hex()
        else:
            url = base

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            print(f"Tracker scrape failed: {e}")
            return {'files': {}}
