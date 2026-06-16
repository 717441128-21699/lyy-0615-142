import os
import json
import hashlib
import random
from typing import List, Dict, Set, Optional, Tuple
from collections import defaultdict

from protocol import (
    Bitfield, TorrentInfo, PIECE_SIZE,
    compute_info_hash, generate_peer_id
)

BLOCK_SIZE = 16 * 1024


def save_torrent_file(torrent_info: TorrentInfo, output_path: str,
                      tracker_url: str = None, created_by: str = "P2P v1.0"):
    torrent_dict = {
        'announce': tracker_url or '',
        'created by': created_by,
        'creation date': int(os.path.getmtime(output_path)) if os.path.exists(output_path) else int(time.time()),
        'info': {
            'name': torrent_info.filename,
            'length': torrent_info.total_size,
            'piece length': torrent_info.piece_size,
            'pieces': [h.hex() for h in torrent_info.piece_hashes]
        },
        'info_hash': torrent_info.info_hash.hex()
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(torrent_dict, f, indent=2, ensure_ascii=False)


def load_torrent_file(filepath: str) -> Tuple[TorrentInfo, Optional[str]]:
    with open(filepath, 'r', encoding='utf-8') as f:
        torrent_dict = json.load(f)

    info = torrent_dict['info']
    filename = info['name']
    total_size = info['length']
    piece_size = info['piece length']
    piece_hashes = [bytes.fromhex(h) for h in info['pieces']]

    info_hash = compute_info_hash(filename, total_size, piece_size, piece_hashes)

    if 'info_hash' in torrent_dict:
        stored_hash = bytes.fromhex(torrent_dict['info_hash'])
        if stored_hash != info_hash:
            print(f"Warning: info_hash mismatch! Recomputed: {info_hash.hex()}")

    tracker_url = torrent_dict.get('announce') or None

    torrent_info = TorrentInfo(
        filename=filename,
        total_size=total_size,
        piece_size=piece_size,
        piece_hashes=piece_hashes,
        info_hash=info_hash
    )

    return torrent_info, tracker_url


import time


class PieceManager:
    def __init__(self, torrent_info: TorrentInfo, download_dir: str = "./downloads",
                 source_filepath: str = None, fast_resume: bool = True):
        self.torrent_info = torrent_info
        self.download_dir = download_dir
        os.makedirs(download_dir, exist_ok=True)

        self.num_pieces = torrent_info.num_pieces
        self.bitfield = Bitfield(self.num_pieces)
        self.pieces: Dict[int, bytearray] = {}
        self.requested: Set[int] = set()
        self.received_blocks: Dict[int, Set[int]] = {}

        self.peer_bitfields: Dict[bytes, Bitfield] = {}
        self.piece_availability: List[int] = [0] * self.num_pieces

        self.source_filepath = source_filepath
        self.output_path = os.path.join(download_dir, torrent_info.filename)

        if source_filepath:
            self.output_path = source_filepath
            self._verify_source_file()
        else:
            self._init_file_storage()
            if fast_resume:
                self._scan_existing_pieces()

    def _init_file_storage(self):
        if not os.path.exists(self.output_path):
            with open(self.output_path, 'wb') as f:
                f.truncate(self.torrent_info.total_size)

    def _verify_source_file(self):
        if not os.path.exists(self.source_filepath):
            raise FileNotFoundError(f"Source file not found: {self.source_filepath}")

        actual_size = os.path.getsize(self.source_filepath)
        if actual_size != self.torrent_info.total_size:
            raise ValueError(f"Source file size mismatch: expected {self.torrent_info.total_size}, got {actual_size}")

        print(f"Verifying source file: {self.source_filepath}")
        all_valid = True
        with open(self.source_filepath, 'rb') as f:
            for i in range(self.num_pieces):
                f.seek(i * self.torrent_info.piece_size)
                data = f.read(self._get_piece_size(i))
                h = hashlib.sha1(data).digest()
                if h == self.torrent_info.piece_hashes[i]:
                    self.bitfield.set_piece(i, True)
                else:
                    all_valid = False
                    print(f"  Warning: piece {i} hash mismatch")

        if all_valid:
            print(f"  All {self.num_pieces} pieces verified ✓")
        else:
            valid_count = self.bitfield.count_set()
            print(f"  {valid_count}/{self.num_pieces} pieces valid")

    def _scan_existing_pieces(self):
        if not os.path.exists(self.output_path):
            return

        actual_size = os.path.getsize(self.output_path)
        if actual_size < self.torrent_info.total_size:
            print(f"File incomplete ({actual_size}/{self.torrent_info.total_size} bytes), scanning...")

        verified = 0
        with open(self.output_path, 'rb') as f:
            for i in range(self.num_pieces):
                if self.bitfield.has_piece(i):
                    verified += 1
                    continue
                f.seek(i * self.torrent_info.piece_size)
                data = f.read(self._get_piece_size(i))
                if len(data) == self._get_piece_size(i):
                    h = hashlib.sha1(data).digest()
                    if h == self.torrent_info.piece_hashes[i]:
                        self.bitfield.set_piece(i, True)
                        verified += 1

        if verified > 0:
            print(f"Fast resume: {verified}/{self.num_pieces} pieces already verified ✓")

    def add_peer(self, peer_id: bytes, bitfield: Bitfield):
        self.peer_bitfields[peer_id] = bitfield
        for i in range(self.num_pieces):
            if bitfield.has_piece(i):
                self.piece_availability[i] += 1

    def remove_peer(self, peer_id: bytes):
        if peer_id in self.peer_bitfields:
            bf = self.peer_bitfields[peer_id]
            for i in range(self.num_pieces):
                if bf.has_piece(i):
                    self.piece_availability[i] -= 1
            del self.peer_bitfields[peer_id]

    def peer_has_piece(self, peer_id: bytes, piece_index: int) -> bool:
        if peer_id not in self.peer_bitfields:
            return False
        return self.peer_bitfields[peer_id].has_piece(piece_index)

    def update_peer_have(self, peer_id: bytes, piece_index: int):
        if peer_id not in self.peer_bitfields:
            self.peer_bitfields[peer_id] = Bitfield(self.num_pieces)
        bf = self.peer_bitfields[peer_id]
        if not bf.has_piece(piece_index):
            bf.set_piece(piece_index, True)
            self.piece_availability[piece_index] += 1

    def have_piece(self, index: int) -> bool:
        return self.bitfield.has_piece(index)

    def is_complete(self) -> bool:
        return self.bitfield.is_complete()

    def get_piece_data(self, index: int) -> Optional[bytes]:
        if not self.have_piece(index):
            return None
        if index in self.pieces:
            return bytes(self.pieces[index])
        return self._read_piece_from_disk(index)

    def _read_piece_from_disk(self, index: int) -> bytes:
        offset = index * self.torrent_info.piece_size
        size = self._get_piece_size(index)
        with open(self.output_path, 'rb') as f:
            f.seek(offset)
            return f.read(size)

    def _get_piece_size(self, index: int) -> int:
        if index == self.num_pieces - 1:
            last = self.torrent_info.total_size % self.torrent_info.piece_size
            return last if last > 0 else self.torrent_info.piece_size
        return self.torrent_info.piece_size

    def _verify_piece(self, index: int, data: bytes) -> bool:
        h = hashlib.sha1(data).digest()
        return h == self.torrent_info.piece_hashes[index]

    def receive_block(self, piece_index: int, begin: int, data: bytes) -> bool:
        if self.have_piece(piece_index):
            return False

        if piece_index not in self.pieces:
            piece_size = self._get_piece_size(piece_index)
            self.pieces[piece_index] = bytearray(piece_size)
            self.received_blocks[piece_index] = set()

        self.pieces[piece_index][begin:begin + len(data)] = data
        self.received_blocks[piece_index].add(begin)

        if self._is_piece_complete(piece_index):
            piece_data = bytes(self.pieces[piece_index])
            if self._verify_piece(piece_index, piece_data):
                self._write_piece_to_disk(piece_index, piece_data)
                self.bitfield.set_piece(piece_index, True)
                if piece_index in self.requested:
                    self.requested.remove(piece_index)
                del self.pieces[piece_index]
                del self.received_blocks[piece_index]
                return True
            else:
                del self.pieces[piece_index]
                del self.received_blocks[piece_index]
                if piece_index in self.requested:
                    self.requested.remove(piece_index)
                return False
        return False

    def _is_piece_complete(self, piece_index: int) -> bool:
        if piece_index not in self.received_blocks:
            return False
        received = self.received_blocks[piece_index]
        expected_size = self._get_piece_size(piece_index)
        full_blocks = expected_size // BLOCK_SIZE
        last_block_size = expected_size % BLOCK_SIZE
        expected_blocks = full_blocks + (1 if last_block_size > 0 else 0)
        return len(received) >= expected_blocks

    def _write_piece_to_disk(self, index: int, data: bytes):
        offset = index * self.torrent_info.piece_size
        with open(self.output_path, 'r+b') as f:
            f.seek(offset)
            f.write(data)

    def select_piece_rarest_first(self, peer_id: bytes, exclude_requested: bool = True) -> Optional[int]:
        if peer_id not in self.peer_bitfields:
            return None

        peer_bf = self.peer_bitfields[peer_id]
        candidates = []

        for i in range(self.num_pieces):
            if self.bitfield.has_piece(i):
                continue
            if not peer_bf.has_piece(i):
                continue
            if exclude_requested and i in self.requested:
                continue
            candidates.append((self.piece_availability[i], i))

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x[0], random.random()))

        rarest_count = candidates[0][0]
        rarest_pieces = [idx for cnt, idx in candidates if cnt == rarest_count]
        chosen = random.choice(rarest_pieces)

        self.requested.add(chosen)
        return chosen

    def select_endgame_pieces(self, peer_id: bytes, max_requests: int = 5) -> List[int]:
        if peer_id not in self.peer_bitfields:
            return []

        peer_bf = self.peer_bitfields[peer_id]
        missing = []
        for i in range(self.num_pieces):
            if self.bitfield.has_piece(i):
                continue
            if not peer_bf.has_piece(i):
                continue
            missing.append(i)

        if len(missing) <= max_requests:
            return missing
        return random.sample(missing, max_requests)

    def mark_requested(self, piece_index: int):
        self.requested.add(piece_index)

    def unmark_requested(self, piece_index: int):
        self.requested.discard(piece_index)

    def clear_all_requested(self):
        self.requested.clear()

    def progress(self) -> Tuple[int, int]:
        downloaded = self.bitfield.count_set()
        return downloaded, self.num_pieces

    def progress_percent(self) -> float:
        d, t = self.progress()
        if t == 0:
            return 0.0
        return (d / t) * 100

    @staticmethod
    def create_torrent_from_file(filepath: str, piece_size: int = PIECE_SIZE) -> TorrentInfo:
        filename = os.path.basename(filepath)
        total_size = os.path.getsize(filepath)

        num_pieces = (total_size + piece_size - 1) // piece_size
        piece_hashes = []

        print(f"Creating torrent for: {filename} ({total_size} bytes)")
        with open(filepath, 'rb') as f:
            for i in range(num_pieces):
                data = f.read(piece_size)
                h = hashlib.sha1(data).digest()
                piece_hashes.append(h)

        info_hash = compute_info_hash(filename, total_size, piece_size, piece_hashes)
        print(f"  {num_pieces} pieces, info_hash: {info_hash.hex()[:16]}...")
        return TorrentInfo(
            filename=filename,
            total_size=total_size,
            piece_size=piece_size,
            piece_hashes=piece_hashes,
            info_hash=info_hash
        )

    @classmethod
    def from_existing_file(cls, filepath: str, download_dir: str = None) -> 'PieceManager':
        torrent_info = cls.create_torrent_from_file(filepath)
        return cls(
            torrent_info=torrent_info,
            download_dir=download_dir or os.path.dirname(os.path.abspath(filepath)),
            source_filepath=filepath,
            fast_resume=True
        )

    @classmethod
    def from_torrent_file(cls, torrent_filepath: str, download_dir: str = "./downloads",
                          source_filepath: str = None) -> Tuple['PieceManager', Optional[str]]:
        torrent_info, tracker_url = load_torrent_file(torrent_filepath)
        pm = cls(
            torrent_info=torrent_info,
            download_dir=download_dir,
            source_filepath=source_filepath,
            fast_resume=(source_filepath is None)
        )
        return pm, tracker_url

    def save_torrent(self, output_path: str, tracker_url: str = None):
        save_torrent_file(self.torrent_info, output_path, tracker_url)

    def get_peers_with_piece(self, piece_index: int) -> List[bytes]:
        peers = []
        for peer_id, bf in self.peer_bitfields.items():
            if bf.has_piece(piece_index):
                peers.append(peer_id)
        return peers

    def get_rarest_pieces(self, limit: int = 10) -> List[Tuple[int, int]]:
        result = []
        for i in range(self.num_pieces):
            if not self.bitfield.has_piece(i):
                result.append((self.piece_availability[i], i))
        result.sort(key=lambda x: x[0])
        return result[:limit]
