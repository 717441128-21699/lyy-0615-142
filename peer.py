import socket
import threading
import time
import random
import struct
import os
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict, deque

from protocol import *
from piece_manager import PieceManager, load_torrent_file
from tracker import TrackerClient


BLOCK_SIZE = 16 * 1024
MAX_REQUESTS_PER_PEER = 5
MAX_UNCHOKED_PEERS = 2
CHOKE_INTERVAL = 5
OPTIMISTIC_UNCHOKE_INTERVAL = 5
RATE_WINDOW_SECONDS = 20
FREE_RIDER_UPLOAD_LIMIT = 512
DEFAULT_UPLOAD_LIMIT = 200 * 1024  # 1KB/s max upload for free-riders


class RateMeter:
    def __init__(self, window_seconds: int = RATE_WINDOW_SECONDS):
        self.window_seconds = window_seconds
        self.samples: deque = deque()
        self.total = 0
        self.lock = threading.Lock()

    def add_bytes(self, num_bytes: int):
        with self.lock:
            now = time.time()
            self.samples.append((now, num_bytes))
            self.total += num_bytes
            self._cleanup(now)

    def _cleanup(self, now: float):
        cutoff = now - self.window_seconds
        while self.samples and self.samples[0][0] < cutoff:
            _, n = self.samples.popleft()
            self.total -= n

    def get_rate(self) -> float:
        with self.lock:
            now = time.time()
            self._cleanup(now)
            if not self.samples:
                return 0.0
            timespan = max(1.0, now - self.samples[0][0])
            return self.total / timespan

    def get_total(self) -> int:
        with self.lock:
            return self.total


class PeerConnection:
    def __init__(self, sock: socket.socket, peer_id: bytes = None, addr: tuple = None):
        self.sock = sock
        self.peer_id = peer_id
        self.addr = addr
        self.buffer = b''

        self.handshake_done = False
        self.bitfield: Optional[Bitfield] = None

        self.am_choking = True
        self.am_interested = False
        self.peer_choking = True
        self.peer_interested = False

        self.requested_pieces: List[tuple] = []
        self.pending_requests: Set[int] = set()

        self.download_meter = RateMeter()
        self.upload_meter = RateMeter()

        self.is_seed = False
        self.sent_handshake = False

        self.free_rider_mode = False
        self.upload_limit = DEFAULT_UPLOAD_LIMIT

        self.lock = threading.Lock()

    @property
    def downloaded(self) -> int:
        return self.download_meter.get_total()

    @property
    def uploaded(self) -> int:
        return self.upload_meter.get_total()

    @property
    def download_rate(self) -> float:
        return self.download_meter.get_rate()

    @property
    def upload_rate(self) -> float:
        return self.upload_meter.get_rate()

    def send_data(self, data: bytes):
        with self.lock:
            try:
                self.sock.sendall(data)
            except Exception:
                pass

    def send_message(self, msg_type: int, payload: bytes = b''):
        msg = encode_message(msg_type, payload)
        self.send_data(msg)

    def send_handshake(self, info_hash: bytes, peer_id: bytes):
        if self.sent_handshake:
            return
        msg = encode_handshake(info_hash, peer_id)
        self.send_data(msg)
        self.sent_handshake = True

    def send_bitfield(self, bitfield: Bitfield):
        self.send_message(MSG_BITFIELD, bitfield.to_bytes())

    def send_have(self, piece_index: int):
        self.send_message(MSG_HAVE, struct.pack(">I", piece_index))

    def send_request(self, piece_index: int, begin: int, length: int):
        payload = struct.pack(">III", piece_index, begin, length)
        self.send_message(MSG_REQUEST, payload)

    def send_piece(self, piece_index: int, begin: int, data: bytes, peer=None):
        if peer is not None:
            peer.acquire_upload_tokens(len(data))

        payload = struct.pack(">II", piece_index, begin) + data
        self.send_message(MSG_PIECE, payload)
        self.upload_meter.add_bytes(len(data))

    def send_interested(self):
        self.send_message(MSG_INTERESTED)
        self.am_interested = True

    def send_not_interested(self):
        self.send_message(MSG_NOT_INTERESTED)
        self.am_interested = False

    def send_choke(self):
        self.send_message(MSG_CHOKE)
        self.am_choking = True

    def send_unchoke(self):
        self.send_message(MSG_UNCHOKE)
        self.am_choking = False

    def send_keepalive(self):
        self.send_data(struct.pack(">I", 0))

    def recv_some(self) -> bool:
        try:
            data = self.sock.recv(65536)
            if not data:
                return False
            self.buffer += data
            return True
        except Exception:
            return False

    def read_messages(self, num_pieces: int) -> List[tuple]:
        messages = []

        while True:
            if not self.handshake_done:
                preamble_len = len(HANDSHAKE_PREAMBLE)
                if len(self.buffer) < preamble_len + 40:
                    break
                result = decode_handshake(self.buffer)
                if result:
                    info_hash, peer_id = result
                    self.peer_id = peer_id
                    self.handshake_done = True
                    self.buffer = self.buffer[preamble_len + 40:]
                    messages.append((MSG_HANDSHAKE, (info_hash, peer_id)))
                else:
                    break
            else:
                result = decode_message(self.buffer)
                if result is None:
                    break
                msg_type, payload, consumed = result
                self.buffer = self.buffer[consumed:]

                if msg_type == MSG_HAVE:
                    piece_idx = decode_have(payload)
                    messages.append((MSG_HAVE, piece_idx))
                elif msg_type == MSG_BITFIELD:
                    bf = decode_bitfield(payload, num_pieces)
                    self.bitfield = bf
                    messages.append((MSG_BITFIELD, bf))
                elif msg_type == MSG_REQUEST:
                    piece_idx, begin, length = decode_request(payload)
                    messages.append((MSG_REQUEST, (piece_idx, begin, length)))
                elif msg_type == MSG_PIECE:
                    piece_idx, begin, data = decode_piece(payload)
                    self.download_meter.add_bytes(len(data))
                    messages.append((MSG_PIECE, (piece_idx, begin, data)))
                elif msg_type == MSG_INTERESTED:
                    self.peer_interested = True
                    messages.append((MSG_INTERESTED, None))
                elif msg_type == MSG_NOT_INTERESTED:
                    self.peer_interested = False
                    messages.append((MSG_NOT_INTERESTED, None))
                elif msg_type == MSG_CHOKE:
                    self.peer_choking = True
                    messages.append((MSG_CHOKE, None))
                elif msg_type == MSG_UNCHOKE:
                    self.peer_choking = False
                    messages.append((MSG_UNCHOKE, None))
                elif msg_type == MSG_KEEPALIVE:
                    messages.append((MSG_KEEPALIVE, None))

        return messages

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class Peer:
    def __init__(self, host: str, port: int, peer_id: bytes = None,
                 download_dir: str = "./downloads", tracker_url: str = None,
                 free_rider: bool = False, upload_limit: int = DEFAULT_UPLOAD_LIMIT):
        self.host = host
        self.port = port
        self.peer_id = peer_id or generate_peer_id()
        self.download_dir = download_dir
        self.tracker_url = tracker_url
        self.free_rider_mode = free_rider
        self.upload_limit = upload_limit

        self.piece_manager: Optional[PieceManager] = None
        self.torrent_info: Optional[TorrentInfo] = None

        self.connections: Dict[bytes, PeerConnection] = {}
        self.connection_by_addr: Dict[str, PeerConnection] = {}

        self.server_sock: Optional[socket.socket] = None
        self.running = False

        self.server_thread: Optional[threading.Thread] = None
        self.main_loop_thread: Optional[threading.Thread] = None

        self.download_meter = RateMeter()
        self.upload_meter = RateMeter()

        self.unchoked_peers: Set[bytes] = set()
        self.optimistic_peer: Optional[bytes] = None
        self.last_choke_update = 0
        self.last_optimistic_update = 0

        self.announce_interval = 30
        self.last_announce = 0

        self.lock = threading.Lock()

        self.shared_upload_tokens = float(upload_limit)
        self.shared_upload_last_refill = time.time()
        self.upload_bucket_lock = threading.Lock()

        self.seed_after_complete = True
        self.completed_callback = None
        self.progress_callback = None
        self.status_callback = None

        self.start_time = 0

    def acquire_upload_tokens(self, data_len: int):
        effective_limit = self.upload_limit
        while True:
            with self.upload_bucket_lock:
                now = time.time()
                elapsed = now - self.shared_upload_last_refill
                self.shared_upload_tokens = min(
                    float(effective_limit * 2),
                    self.shared_upload_tokens + elapsed * effective_limit
                )
                self.shared_upload_last_refill = now

                if self.shared_upload_tokens >= data_len:
                    self.shared_upload_tokens -= data_len
                    return

                deficit = data_len - self.shared_upload_tokens
                wait_time = deficit / effective_limit

            time.sleep(wait_time)

    @classmethod
    def from_torrent_file(cls, torrent_filepath: str, host: str = '127.0.0.1',
                          port: int = 0, download_dir: str = "./downloads",
                          source_filepath: str = None, seed: bool = False,
                          free_rider: bool = False,
                          upload_limit: int = DEFAULT_UPLOAD_LIMIT) -> 'Peer':
        torrent_info, tracker_url = load_torrent_file(torrent_filepath)
        peer = cls(host=host, port=port, download_dir=download_dir,
                   tracker_url=tracker_url, free_rider=free_rider,
                   upload_limit=upload_limit)

        peer.piece_manager = PieceManager(
            torrent_info=torrent_info,
            download_dir=download_dir,
            source_filepath=source_filepath,
            fast_resume=(not seed)
        )
        peer.torrent_info = torrent_info

        if seed and not source_filepath:
            for i in range(torrent_info.num_pieces):
                peer.piece_manager.bitfield.set_piece(i, True)

        return peer

    def load_torrent(self, torrent_info: TorrentInfo, seed: bool = False):
        self.torrent_info = torrent_info
        self.piece_manager = PieceManager(torrent_info, self.download_dir)

        if seed:
            for i in range(torrent_info.num_pieces):
                self.piece_manager.bitfield.set_piece(i, True)

    def seed_file(self, filepath: str):
        self.piece_manager = PieceManager.from_existing_file(filepath, self.download_dir)
        self.torrent_info = self.piece_manager.torrent_info

    def pause(self):
        if self.piece_manager:
            self.piece_manager.clear_all_requested()
        with self.lock:
            for conn in self.connections.values():
                conn.requested_pieces.clear()
                conn.pending_requests.clear()

    def resume(self):
        pass

    def start(self):
        self.running = True
        self.start_time = time.time()

        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(50)
        self.server_sock.settimeout(1.0)

        if self.free_rider_mode:
            print(f"⚠ Free-rider mode: upload limited to {FREE_RIDER_UPLOAD_LIMIT}B/s")

        self.server_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.server_thread.start()

        self.main_loop_thread = threading.Thread(target=self._main_loop, daemon=True)
        self.main_loop_thread.start()

        port = self.get_listen_port()
        if self.piece_manager and self.piece_manager.is_complete():
            prog_str = f"{self.piece_manager.num_pieces}/{self.piece_manager.num_pieces} (seeding)"
        elif self.piece_manager:
            d, t = self.piece_manager.progress()
            prog_str = f"{d}/{t}"
        else:
            prog_str = "?"
        print(f"▶ Peer {self.peer_id.hex()[:8]} started on {self.host}:{port} | {prog_str}")

    def stop(self):
        self.running = False
        self.pause()

        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass

        with self.lock:
            for conn in self.connections.values():
                conn.close()
            self.connections.clear()

        self._announce_to_tracker("stopped")
        print(f"⏹ Peer {self.peer_id.hex()[:8]} stopped")

    def connect_to_peer(self, ip: str, port: int) -> Optional[PeerConnection]:
        addr_key = f"{ip}:{port}"
        with self.lock:
            if addr_key in self.connection_by_addr:
                return self.connection_by_addr[addr_key]

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((ip, port))
            sock.settimeout(None)

            conn = PeerConnection(sock, addr=(ip, port))
            conn.free_rider_mode = self.free_rider_mode
            conn.send_handshake(self.torrent_info.info_hash, self.peer_id)

            t = threading.Thread(target=self._handle_connection, args=(conn,), daemon=True)
            t.start()

            return conn
        except Exception as e:
            return None

    def _accept_loop(self):
        while self.running:
            try:
                client_sock, addr = self.server_sock.accept()
                conn = PeerConnection(client_sock, addr=addr)
                conn.free_rider_mode = self.free_rider_mode

                t = threading.Thread(target=self._handle_connection, args=(conn,), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except OSError:
                if not self.running:
                    break
                try:
                    if self.server_sock:
                        self.server_sock.fileno()
                except:
                    break
            except Exception:
                if not self.running:
                    break

    def _handle_connection(self, conn: PeerConnection):
        try:
            while self.running:
                if not conn.recv_some():
                    break

                num_pieces = self.torrent_info.num_pieces if self.torrent_info else 100
                messages = conn.read_messages(num_pieces)

                for msg_type, data in messages:
                    try:
                        self._handle_message(conn, msg_type, data)
                    except Exception as e:
                        pass
        except Exception:
            pass
        finally:
            self._remove_connection(conn)

    def _handle_message(self, conn: PeerConnection, msg_type: int, data):
        if not self.torrent_info:
            return

        if msg_type == MSG_HANDSHAKE:
            info_hash, peer_id = data
            if info_hash != self.torrent_info.info_hash:
                conn.close()
                return

            conn.send_handshake(self.torrent_info.info_hash, self.peer_id)
            if self.piece_manager:
                conn.send_bitfield(self.piece_manager.bitfield)

            with self.lock:
                self.connections[peer_id] = conn
                addr_key = f"{conn.addr[0]}:{conn.addr[1]}" if conn.addr else peer_id.hex()
                self.connection_by_addr[addr_key] = conn

            if self.piece_manager:
                self.piece_manager.add_peer(peer_id, Bitfield(self.torrent_info.num_pieces))

            if not conn.bitfield and not self.piece_manager.is_complete():
                self._check_interested(conn)

        elif msg_type == MSG_BITFIELD:
            bf = data
            conn.bitfield = bf
            if self.piece_manager and conn.peer_id:
                self.piece_manager.remove_peer(conn.peer_id)
                self.piece_manager.add_peer(conn.peer_id, bf)

            conn.is_seed = bf.is_complete()
            self._check_interested(conn)

        elif msg_type == MSG_HAVE:
            piece_idx = data
            if self.piece_manager and conn.peer_id:
                self.piece_manager.update_peer_have(conn.peer_id, piece_idx)

            if not conn.am_interested:
                self._check_interested(conn)

        elif msg_type == MSG_INTERESTED:
            self._update_choke(force=True)

        elif msg_type == MSG_NOT_INTERESTED:
            pass

        elif msg_type == MSG_CHOKE:
            for piece_idx, begin, _ in list(conn.requested_pieces):
                if self.piece_manager:
                    self.piece_manager.unmark_requested(piece_idx)
            conn.requested_pieces.clear()
            conn.pending_requests.clear()

        elif msg_type == MSG_UNCHOKE:
            self._fill_requests(conn)

        elif msg_type == MSG_REQUEST:
            piece_idx, begin, length = data
            if not conn.am_choking and self.piece_manager:
                piece_data = self.piece_manager.get_piece_data(piece_idx)
                if piece_data:
                    block_data = piece_data[begin:begin + length]
                    conn.send_piece(piece_idx, begin, block_data, peer=self)
                    self.upload_meter.add_bytes(len(block_data))

        elif msg_type == MSG_PIECE:
            piece_idx, begin, data = data

            conn.requested_pieces = [
                r for r in conn.requested_pieces
                if not (r[0] == piece_idx and r[1] == begin)
            ]
            conn.pending_requests.discard(piece_idx)
            self.download_meter.add_bytes(len(data))

            if self.piece_manager:
                completed = self.piece_manager.receive_block(piece_idx, begin, data)

                if completed:
                    self._broadcast_have(piece_idx)

                    if self.progress_callback:
                        downloaded, total = self.piece_manager.progress()
                        self.progress_callback(downloaded, total)

                    if self.piece_manager.is_complete():
                        with self.lock:
                            for c in self.connections.values():
                                if c.am_interested:
                                    c.send_not_interested()
                        if self.completed_callback:
                            self.completed_callback()
                        if not self.seed_after_complete:
                            self.stop()

            self._fill_requests(conn)

        elif msg_type == MSG_KEEPALIVE:
            pass

    def _check_interested(self, conn: PeerConnection):
        if not conn.bitfield or not self.piece_manager:
            return

        interested = False
        for i in range(self.torrent_info.num_pieces):
            if not self.piece_manager.have_piece(i) and conn.bitfield.has_piece(i):
                interested = True
                break

        if interested and not conn.am_interested:
            conn.send_interested()
        elif not interested and conn.am_interested:
            conn.send_not_interested()

    def _fill_requests(self, conn: PeerConnection):
        if conn.peer_choking or not self.piece_manager or not conn.bitfield:
            return

        while len(conn.requested_pieces) < MAX_REQUESTS_PER_PEER:
            piece_idx = self.piece_manager.select_piece_rarest_first(conn.peer_id)
            if piece_idx is None:
                break

            piece_size = self._get_piece_size(piece_idx)
            for begin in range(0, piece_size, BLOCK_SIZE):
                block_len = min(BLOCK_SIZE, piece_size - begin)
                conn.send_request(piece_idx, begin, block_len)
                conn.requested_pieces.append((piece_idx, begin, block_len))
                conn.pending_requests.add(piece_idx)

    def _get_piece_size(self, piece_index: int) -> int:
        if not self.torrent_info:
            return 0
        if piece_index == self.torrent_info.num_pieces - 1:
            last = self.torrent_info.total_size % self.torrent_info.piece_size
            return last if last > 0 else self.torrent_info.piece_size
        return self.torrent_info.piece_size

    def _broadcast_have(self, piece_idx: int):
        with self.lock:
            for conn in self.connections.values():
                conn.send_have(piece_idx)

    def _remove_connection(self, conn: PeerConnection):
        with self.lock:
            if conn.peer_id and conn.peer_id in self.connections:
                del self.connections[conn.peer_id]
            if conn.addr:
                addr_key = f"{conn.addr[0]}:{conn.addr[1]}"
                if addr_key in self.connection_by_addr:
                    del self.connection_by_addr[addr_key]

            if self.piece_manager and conn.peer_id:
                self.piece_manager.remove_peer(conn.peer_id)

            if conn.peer_id in self.unchoked_peers:
                self.unchoked_peers.discard(conn.peer_id)
            if self.optimistic_peer == conn.peer_id:
                self.optimistic_peer = None

    def _is_non_contributor(self, conn: PeerConnection) -> bool:
        elapsed = time.time() - self.start_time if self.start_time > 0 else 0
        if elapsed < 3:
            return False
        if conn.downloaded < 512 and conn.download_rate < 50:
            return True
        return False

    def _update_choke(self, force: bool = False):
        now = time.time()

        if not force and now - self.last_choke_update < CHOKE_INTERVAL:
            return
        self.last_choke_update = now

        if self.free_rider_mode:
            return

        interested_peers = []
        with self.lock:
            for peer_id, conn in self.connections.items():
                if conn.peer_interested:
                    interested_peers.append((peer_id, conn))

        is_seed = self.piece_manager and self.piece_manager.is_complete()

        if is_seed:
            for peer_id, conn in interested_peers:
                if conn.am_choking:
                    conn.send_unchoke()
                self.unchoked_peers.add(peer_id)
            all_interested = {pid for pid, _ in interested_peers}
            for peer_id in list(self.unchoked_peers):
                if peer_id not in all_interested:
                    conn = self.connections.get(peer_id)
                    if conn and not conn.am_choking:
                        conn.send_choke()
                    self.unchoked_peers.discard(peer_id)
            return

        if not interested_peers:
            return

        if len(interested_peers) <= MAX_UNCHOKED_PEERS:
            for peer_id, conn in interested_peers:
                if conn.am_choking:
                    conn.send_unchoke()
                self.unchoked_peers.add(peer_id)
            return

        elapsed = now - self.start_time if self.start_time > 0 else 0

        peers_with_score = []
        for peer_id, conn in interested_peers:
            if self._is_non_contributor(conn):
                score = -1.0
            else:
                score = conn.download_rate
                if score < 1.0:
                    if elapsed > 3 and conn.downloaded < 512:
                        score = 0.001
                    else:
                        score = 0.01
            peers_with_score.append((score, peer_id, conn))

        peers_with_score.sort(key=lambda x: (-x[0], random.random()))

        new_unchoked = set()
        count = 0
        for score, peer_id, conn in peers_with_score:
            if count >= MAX_UNCHOKED_PEERS:
                break
            if conn.am_choking:
                conn.send_unchoke()
            new_unchoked.add(peer_id)
            count += 1

        if now - self.last_optimistic_update >= OPTIMISTIC_UNCHOKE_INTERVAL:
            self.last_optimistic_update = now
            remaining = [
                (peer_id, conn) for peer_id, conn in interested_peers
                if peer_id not in new_unchoked
            ]

            contributors = [
                (pid, c) for pid, c in remaining
                if not self._is_non_contributor(c)
            ]
            if contributors:
                opt_peer_id, opt_conn = random.choice(contributors)
                if opt_conn.am_choking:
                    opt_conn.send_unchoke()
                new_unchoked.add(opt_peer_id)
                self.optimistic_peer = opt_peer_id
            else:
                self.optimistic_peer = None
        elif self.optimistic_peer:
            opt_peer_id = self.optimistic_peer
            if opt_peer_id in [p[0] for p in interested_peers]:
                opt_conn = next(p[1] for p in interested_peers if p[0] == opt_peer_id)
                if not self._is_non_contributor(opt_conn):
                    if opt_conn.am_choking:
                        opt_conn.send_unchoke()
                    new_unchoked.add(opt_peer_id)

        for peer_id, conn in interested_peers:
            if peer_id not in new_unchoked and not conn.am_choking:
                conn.send_choke()

        self.unchoked_peers = new_unchoked

    def _announce_to_tracker(self, event: str = "started"):
        if not self.tracker_url or not self.torrent_info:
            return

        left = 0
        if self.piece_manager:
            downloaded = self.piece_manager.bitfield.count_set()
            left = (self.torrent_info.num_pieces - downloaded) * self.torrent_info.piece_size

        try:
            client = TrackerClient(self.tracker_url)
            result = client.announce(
                info_hash=self.torrent_info.info_hash,
                peer_id=self.peer_id,
                port=self.get_listen_port(),
                event=event,
                uploaded=self.upload_meter.get_total(),
                downloaded=self.download_meter.get_total(),
                left=left
            )

            if 'interval' in result:
                self.announce_interval = result['interval']

            peers = result.get('peers', [])
            for peer_info in peers:
                ip = peer_info.get('ip', '')
                port = peer_info.get('port', 0)
                listen_port = self.get_listen_port()
                if ip and port and not (ip == self.host and port == listen_port):
                    self.connect_to_peer(ip, port)
        except Exception:
            pass

    def _main_loop(self):
        first_loop = True
        try:
            while self.running:
                time.sleep(1)

                try:
                    self._update_choke(force=first_loop)
                    first_loop = False
                except Exception:
                    pass

                try:
                    if self.status_callback:
                        self.status_callback(self.get_status())
                except Exception:
                    pass

                try:
                    now = time.time()
                    if now - self.last_announce >= self.announce_interval:
                        self.last_announce = now
                        if self.piece_manager and self.piece_manager.is_complete():
                            self._announce_to_tracker("completed")
                        else:
                            self._announce_to_tracker("started")
                except Exception:
                    pass
        except Exception:
            pass

    def get_status(self) -> dict:
        if not self.piece_manager or not self.torrent_info:
            return {}

        d, t = self.piece_manager.progress()
        download_rate = self.download_meter.get_rate()
        upload_rate = self.upload_meter.get_rate()

        peer_stats = {}
        with self.lock:
            for pid, conn in self.connections.items():
                pid_short = pid.hex()[:8]
                peer_stats[pid_short] = {
                    'up': conn.uploaded,
                    'down': conn.downloaded,
                    'up_rate': round(conn.upload_rate),
                    'down_rate': round(conn.download_rate),
                    'choking': conn.peer_choking,
                    'choked': conn.am_choking,
                    'interested': conn.peer_interested,
                    'interesting': conn.am_interested,
                    'is_seed': conn.is_seed,
                }

        return {
            'peer_id': self.peer_id.hex()[:8],
            'filename': self.torrent_info.filename,
            'info_hash': self.torrent_info.info_hash.hex()[:16],
            'progress': (d, t),
            'percent': round(d / t * 100, 1) if t > 0 else 0,
            'download_rate': round(download_rate),
            'upload_rate': round(upload_rate),
            'download_total': self.download_meter.get_total(),
            'upload_total': self.upload_meter.get_total(),
            'num_peers': len(self.connections),
            'peers': peer_stats,
            'is_seed': self.piece_manager.is_complete(),
            'uptime': int(time.time() - self.start_time) if self.start_time else 0,
            'free_rider': self.free_rider_mode
        }

    def get_progress(self) -> Tuple[int, int]:
        if not self.piece_manager:
            return 0, 0
        return self.piece_manager.progress()

    def is_complete(self) -> bool:
        if not self.piece_manager:
            return False
        return self.piece_manager.is_complete()

    def get_listen_port(self) -> int:
        if self.server_sock:
            try:
                return self.server_sock.getsockname()[1]
            except Exception:
                pass
        return self.port if self.port != 0 else 0

    def get_num_peers(self) -> int:
        with self.lock:
            return len(self.connections)

    def get_peer_stats(self) -> dict:
        with self.lock:
            stats = {}
            for peer_id, conn in self.connections.items():
                stats[peer_id.hex()[:8]] = {
                    'up': conn.uploaded,
                    'down': conn.downloaded,
                    'up_rate': conn.upload_rate,
                    'down_rate': conn.download_rate,
                    'choking': conn.peer_choking,
                    'choked': conn.am_choking,
                    'interested': conn.peer_interested,
                    'interesting': conn.am_interested,
                    'is_seed': conn.is_seed,
                }
            return stats
