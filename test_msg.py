import os
import sys
import socket
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol import *


def test_direct_message():
    print("=== Direct Message Test ===")

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(('127.0.0.1', 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]
    print(f"Server on port {port}")

    server_conn = None
    server_ready = threading.Event()

    def server_thread():
        nonlocal server_conn
        conn, addr = server_sock.accept()
        server_conn = conn
        server_ready.set()
        print(f"Server accepted connection from {addr}")

    t = threading.Thread(target=server_thread)
    t.start()

    client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_sock.connect(('127.0.0.1', port))
    print("Client connected")

    server_ready.wait()
    time.sleep(0.1)

    print("\n--- Testing handshake ---")
    info_hash = b'12345678901234567890'
    peer_id_server = b'SERVER00000000000001'
    peer_id_client = b'CLIENT00000000000002'

    client_sock.sendall(encode_handshake(info_hash, peer_id_client))
    print("Client sent handshake")

    data = server_conn.recv(1024)
    print(f"Server received {len(data)} bytes")
    result = decode_handshake(data)
    if result:
        ih, pid = result
        print(f"  info_hash match: {ih == info_hash}")
        print(f"  peer_id: {pid}")
    else:
        print("  Handshake decode FAILED")

    server_conn.sendall(encode_handshake(info_hash, peer_id_server))
    print("Server sent handshake")

    data = client_sock.recv(1024)
    print(f"Client received {len(data)} bytes")
    result = decode_handshake(data)
    if result:
        ih, pid = result
        print(f"  info_hash match: {ih == info_hash}")
        print(f"  peer_id: {pid}")
    else:
        print("  Handshake decode FAILED")

    print("\n--- Testing bitfield ---")
    bf = Bitfield(10)
    bf.set_piece(0)
    bf.set_piece(5)
    bf.set_piece(9)

    server_conn.sendall(encode_bitfield(bf))
    print("Server sent bitfield")

    data = client_sock.recv(1024)
    print(f"Client received {len(data)} bytes")
    msg_type, payload, consumed = decode_message(data)
    print(f"  msg_type: {msg_type} (expected {MSG_BITFIELD})")
    bf2 = decode_bitfield(payload, 10)
    print(f"  Pieces: {[i for i in range(10) if bf2.has_piece(i)]}")

    print("\n--- Testing interested ---")
    server_conn.sendall(encode_interested())
    print("Server sent interested")

    data = client_sock.recv(1024)
    print(f"Client received {len(data)} bytes")
    msg_type, payload, consumed = decode_message(data)
    print(f"  msg_type: {msg_type} (expected {MSG_INTERESTED})")

    print("\n--- Testing request ---")
    server_conn.sendall(encode_request(1, 0, 16384))
    print("Server sent request (piece=1, begin=0, len=16384)")

    data = client_sock.recv(1024)
    print(f"Client received {len(data)} bytes")
    msg_type, payload, consumed = decode_message(data)
    print(f"  msg_type: {msg_type} (expected {MSG_REQUEST})")
    piece_idx, begin, length = decode_request(payload)
    print(f"  piece={piece_idx}, begin={begin}, length={length}")

    print("\n--- Testing piece ---")
    piece_data = b"HELLO" * 100
    server_conn.sendall(encode_piece(1, 0, piece_data))
    print(f"Server sent piece ({len(piece_data)} bytes)")

    data = client_sock.recv(4096)
    print(f"Client received {len(data)} bytes")
    msg_type, payload, consumed = decode_message(data)
    print(f"  msg_type: {msg_type} (expected {MSG_PIECE})")
    piece_idx, begin, data2 = decode_piece(payload)
    print(f"  piece={piece_idx}, begin={begin}, data_len={len(data2)}")
    print(f"  data match: {data2 == piece_data}")

    print("\n--- Testing multiple messages ---")
    msgs = b""
    msgs += encode_have(0)
    msgs += encode_have(1)
    msgs += encode_interested()
    msgs += encode_unchoke()
    server_conn.sendall(msgs)
    print("Server sent 4 messages at once")

    buf = b""
    count = 0
    for _ in range(10):
        data = client_sock.recv(1024)
        buf += data
        while True:
            result = decode_message(buf)
            if result is None:
                break
            msg_type, payload, consumed = result
            buf = buf[consumed:]
            count += 1
            print(f"  Decoded msg #{count}: type={msg_type}")
        if count >= 4:
            break

    print(f"Total decoded: {count} messages")

    client_sock.close()
    server_conn.close()
    server_sock.close()
    t.join()

    print("\nAll tests done!")


if __name__ == "__main__":
    test_direct_message()
