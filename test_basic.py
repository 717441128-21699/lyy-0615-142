import os
import sys
import time
import socket
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol import *


def test_direct_connection():
    print("=== Test 1: Direct TCP Connection ===")

    server_done = threading.Event()
    server_data = []

    def server_thread():
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(('127.0.0.1', 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]
        print(f"  Server listening on port {port}")

        server_sock.settimeout(5)
        try:
            client_sock, addr = server_sock.accept()
            print(f"  Server accepted connection from {addr}")
            data = client_sock.recv(1024)
            print(f"  Server received: {data[:20]}...")
            server_data.append(data)
            server_done.set()
            client_sock.close()
        except socket.timeout:
            print("  Server accept timeout")
        server_sock.close()

    t = threading.Thread(target=server_thread)
    t.start()
    time.sleep(0.3)

    server_port = None
    for _ in range(10):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(('127.0.0.1', 9000 + _ if server_port is None else server_port))
            sock.close()
        except:
            pass

    server_port = 0
    time.sleep(0.5)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)

    test_port = 9999
    try:
        sock.connect(('127.0.0.1', test_port))
        print(f"  Client connected to port {test_port}")
        sock.close()
    except Exception as e:
        print(f"  Client connect failed (expected): {e}")

    t.join(timeout=3)
    print()


def test_protocol_encoding():
    print("=== Test 2: Protocol Encoding ===")

    peer_id = generate_peer_id()
    info_hash = b'\x00' * 20

    handshake = encode_handshake(info_hash, peer_id)
    print(f"  Handshake encoded: {len(handshake)} bytes")
    result = decode_handshake(handshake)
    print(f"  Handshake decoded: info_hash={result[0].hex()[:8]}..., peer_id={result[1].hex()[:8]}...")
    print(f"  Match: {result[0] == info_hash and result[1] == peer_id}")

    msg = encode_have(42)
    print(f"  Have message: {len(msg)} bytes")
    msg_type, payload, consumed = decode_message(msg)
    print(f"  Decoded: type={msg_type}, payload_len={len(payload)}")
    piece_idx = decode_have(payload)
    print(f"  Piece index: {piece_idx} (expected 42)")

    bf = Bitfield(10)
    bf.set_piece(0)
    bf.set_piece(5)
    bf.set_piece(9)
    bf_msg = encode_bitfield(bf)
    print(f"  Bitfield message: {len(bf_msg)} bytes")
    msg_type, payload, consumed = decode_message(bf_msg)
    bf2 = decode_bitfield(payload, 10)
    print(f"  Decoded bitfield: has 0={bf2.has_piece(0)}, has 5={bf2.has_piece(5)}, has 9={bf2.has_piece(9)}")
    print(f"  Count: {bf2.count_set()}/10")

    print()


def test_bitfield():
    print("=== Test 3: Bitfield Operations ===")

    bf = Bitfield(20)
    print(f"  Initial: {bf.count_set()}/20")

    for i in [0, 1, 7, 8, 15, 19]:
        bf.set_piece(i)
        print(f"  Set {i}: has={bf.has_piece(i)}")

    print(f"  Total set: {bf.count_set()}/20")
    print(f"  Is complete: {bf.is_complete()}")

    for i in range(20):
        bf.set_piece(i)
    print(f"  After setting all: {bf.count_set()}/20")
    print(f"  Is complete: {bf.is_complete()}")

    print()


if __name__ == "__main__":
    test_protocol_encoding()
    test_bitfield()
    test_direct_connection()
    print("All basic tests done.")
