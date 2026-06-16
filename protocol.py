import struct
import hashlib
from dataclasses import dataclass
from typing import List, Optional


MSG_HANDSHAKE = 0
MSG_BITFIELD = 1
MSG_HAVE = 2
MSG_REQUEST = 3
MSG_PIECE = 4
MSG_INTERESTED = 5
MSG_NOT_INTERESTED = 6
MSG_CHOKE = 7
MSG_UNCHOKE = 8
MSG_KEEPALIVE = 9

HANDSHAKE_PREAMBLE = b"P2PFILEPROTO"
PIECE_SIZE = 256 * 1024  # 256KB per piece


@dataclass
class TorrentInfo:
    filename: str
    total_size: int
    piece_size: int
    piece_hashes: List[bytes]
    info_hash: bytes

    @property
    def num_pieces(self) -> int:
        return len(self.piece_hashes)


def compute_info_hash(filename: str, total_size: int, piece_size: int, piece_hashes: List[bytes]) -> bytes:
    h = hashlib.sha1()
    h.update(filename.encode())
    h.update(struct.pack(">Q", total_size))
    h.update(struct.pack(">I", piece_size))
    for ph in piece_hashes:
        h.update(ph)
    return h.digest()


class Bitfield:
    def __init__(self, num_pieces: int, data: Optional[bytes] = None):
        self.num_pieces = num_pieces
        num_bytes = (num_pieces + 7) // 8
        if data is not None:
            self.data = bytearray(data[:num_bytes])
            if len(self.data) < num_bytes:
                self.data.extend(b'\x00' * (num_bytes - len(self.data)))
        else:
            self.data = bytearray(num_bytes)

    def has_piece(self, index: int) -> bool:
        if index < 0 or index >= self.num_pieces:
            return False
        byte_idx = index // 8
        bit_idx = 7 - (index % 8)
        return bool(self.data[byte_idx] & (1 << bit_idx))

    def set_piece(self, index: int, value: bool = True):
        if index < 0 or index >= self.num_pieces:
            return
        byte_idx = index // 8
        bit_idx = 7 - (index % 8)
        if value:
            self.data[byte_idx] |= (1 << bit_idx)
        else:
            self.data[byte_idx] &= ~(1 << bit_idx)

    def count_set(self) -> int:
        return sum(bin(b).count('1') for b in self.data)

    def is_complete(self) -> bool:
        full_bytes = self.num_pieces // 8
        count = 0
        for i in range(full_bytes):
            count += bin(self.data[i]).count('1')
        remainder = self.num_pieces % 8
        if remainder > 0:
            mask = (0xFF << (8 - remainder)) & 0xFF
            count += bin(self.data[full_bytes] & mask).count('1')
        return count == self.num_pieces

    def to_bytes(self) -> bytes:
        return bytes(self.data)

    def __len__(self) -> int:
        return self.num_pieces

    def __repr__(self) -> str:
        pieces = [i for i in range(self.num_pieces) if self.has_piece(i)]
        return f"Bitfield({len(pieces)}/{self.num_pieces}: {pieces[:10]}...)"


def encode_handshake(info_hash: bytes, peer_id: bytes) -> bytes:
    if len(info_hash) != 20:
        raise ValueError("info_hash must be 20 bytes")
    if len(peer_id) != 20:
        raise ValueError("peer_id must be 20 bytes")
    return HANDSHAKE_PREAMBLE + info_hash + peer_id


def decode_handshake(data: bytes) -> Optional[tuple]:
    preamble_len = len(HANDSHAKE_PREAMBLE)
    if len(data) < preamble_len + 40:
        return None
    if data[:preamble_len] != HANDSHAKE_PREAMBLE:
        return None
    info_hash = data[preamble_len:preamble_len + 20]
    peer_id = data[preamble_len + 20:preamble_len + 40]
    return info_hash, peer_id


def encode_message(msg_type: int, payload: bytes = b'') -> bytes:
    length = len(payload) + 1
    return struct.pack(">I", length) + struct.pack("B", msg_type) + payload


def decode_message(data: bytes) -> Optional[tuple]:
    if len(data) < 4:
        return None
    length = struct.unpack(">I", data[:4])[0]
    if len(data) < 4 + length:
        return None
    if length == 0:
        return MSG_KEEPALIVE, b'', 4
    msg_type = data[4]
    payload = data[5:4 + length]
    return msg_type, payload, 4 + length


def encode_have(piece_index: int) -> bytes:
    return encode_message(MSG_HAVE, struct.pack(">I", piece_index))


def decode_have(payload: bytes) -> int:
    return struct.unpack(">I", payload)[0]


def encode_request(piece_index: int, begin: int, length: int) -> bytes:
    return encode_message(MSG_REQUEST, struct.pack(">III", piece_index, begin, length))


def decode_request(payload: bytes) -> tuple:
    return struct.unpack(">III", payload)


def encode_piece(piece_index: int, begin: int, data: bytes) -> bytes:
    payload = struct.pack(">II", piece_index, begin) + data
    return encode_message(MSG_PIECE, payload)


def decode_piece(payload: bytes) -> tuple:
    piece_index, begin = struct.unpack(">II", payload[:8])
    data = payload[8:]
    return piece_index, begin, data


def encode_bitfield(bitfield: Bitfield) -> bytes:
    return encode_message(MSG_BITFIELD, bitfield.to_bytes())


def decode_bitfield(payload: bytes, num_pieces: int) -> Bitfield:
    return Bitfield(num_pieces, payload)


def encode_interested() -> bytes:
    return encode_message(MSG_INTERESTED)


def encode_not_interested() -> bytes:
    return encode_message(MSG_NOT_INTERESTED)


def encode_choke() -> bytes:
    return encode_message(MSG_CHOKE)


def encode_unchoke() -> bytes:
    return encode_message(MSG_UNCHOKE)


def encode_keepalive() -> bytes:
    return struct.pack(">I", 0)


def generate_peer_id(prefix: str = "-PP0001-") -> bytes:
    import random
    import string
    suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=20 - len(prefix)))
    return (prefix + suffix).encode()[:20]
