import os
import sys
import time
import socket
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import TrackerServer
from peer import Peer
from piece_manager import PieceManager


def main():
    print("=== Peer Connection Debug ===")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    seeder_dir = os.path.join(base_dir, "test_seeder")
    leecher_dir = os.path.join(base_dir, "test_leecher")
    os.makedirs(seeder_dir, exist_ok=True)
    os.makedirs(leecher_dir, exist_ok=True)

    test_file = os.path.join(seeder_dir, "test.txt")
    with open(test_file, 'w') as f:
        f.write("Hello World! " * 10000)

    torrent_info = PieceManager.create_torrent_from_file(test_file)
    print(f"File: {torrent_info.filename}, size: {torrent_info.total_size}")
    print(f"Pieces: {torrent_info.num_pieces}")

    print("\n--- Starting tracker ---")
    tracker = TrackerServer(host='127.0.0.1', port=0)
    tracker.start(daemon=True)
    time.sleep(0.3)
    tracker_port = tracker.server.server_address[1]
    tracker_url = f"http://127.0.0.1:{tracker_port}/announce"
    print(f"Tracker: {tracker_url}")

    print("\n--- Starting seeder ---")
    seeder = Peer(
        host='127.0.0.1',
        port=0,
        download_dir=seeder_dir,
        tracker_url=tracker_url
    )
    seeder.seed_file(test_file)
    seeder.start()
    time.sleep(0.5)
    seeder_port = seeder.server_sock.getsockname()[1]
    print(f"Seeder port: {seeder_port}")
    print(f"Seeder running: {seeder.running}")
    print(f"Seeder connections: {len(seeder.connections)}")

    print("\n--- Manual connect test ---")
    test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test_sock.settimeout(2)
    try:
        test_sock.connect(('127.0.0.1', seeder_port))
        print(f"  TCP connect OK")
        test_sock.close()
    except Exception as e:
        print(f"  TCP connect FAILED: {e}")

    print("\n--- Starting leecher ---")
    leecher = Peer(
        host='127.0.0.1',
        port=0,
        download_dir=leecher_dir,
        tracker_url=tracker_url
    )
    leecher.load_torrent(torrent_info, seed=False)
    leecher.start()
    time.sleep(0.5)
    leecher_port = leecher.server_sock.getsockname()[1]
    print(f"Leecher port: {leecher_port}")
    print(f"Leecher running: {leecher.running}")

    print("\n--- Waiting for tracker announce ---")
    for i in range(10):
        time.sleep(1)
        print(f"  t={i+1}s: seeder_peers={seeder.get_num_peers()}, leecher_peers={leecher.get_num_peers()}")
        print(f"    seeder conns: {list(seeder.connections.keys())}")
        print(f"    leecher conns: {list(leecher.connections.keys())}")
        if seeder.get_num_peers() > 0 and leecher.get_num_peers() > 0:
            print("  CONNECTED!")
            break

    print("\n--- Tracker status ---")
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{tracker_port}/status") as resp:
            import json
            data = json.loads(resp.read().decode())
            for t in data['torrents']:
                print(f"  {t['info_hash'][:20]}...: {t['num_peers']} peers, {t['complete']} seeds, {t['incomplete']} leechers")
    except Exception as e:
        print(f"  Error: {e}")

    print("\n--- Direct connect from leecher to seeder ---")
    conn = leecher.connect_to_peer('127.0.0.1', seeder_port)
    print(f"  connect result: {conn}")
    time.sleep(2)
    print(f"  After connect: seeder_peers={seeder.get_num_peers()}, leecher_peers={leecher.get_num_peers()}")
    print(f"  Leecher connections: {list(leecher.connections.keys())}")
    print(f"  Seeder connections: {list(seeder.connections.keys())}")

    if leecher.get_num_peers() > 0:
        print("\n--- Checking bitfield exchange ---")
        for pid, conn_obj in leecher.connections.items():
            print(f"  Peer {pid.hex()[:8]}: handshake_done={conn_obj.handshake_done}, bitfield={conn_obj.bitfield}")
            if conn_obj.bitfield:
                print(f"    peer has {conn_obj.bitfield.count_set()} pieces")

    print("\n--- Cleaning up ---")
    seeder.stop()
    leecher.stop()
    tracker.stop()

    print("\nDone.")


if __name__ == "__main__":
    main()
