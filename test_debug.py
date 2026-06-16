import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import TrackerServer, TrackerClient
from peer import Peer
from piece_manager import PieceManager


def main():
    base_dir = tempfile.mkdtemp(prefix="p2p_test_")
    print(f"Working dir: {base_dir}")

    seeder_dir = os.path.join(base_dir, "seeder")
    leecher_dir = os.path.join(base_dir, "leecher")
    os.makedirs(seeder_dir)
    os.makedirs(leecher_dir)

    test_file = os.path.join(seeder_dir, "test.txt")
    with open(test_file, 'w') as f:
        f.write("Hello World! " * 1000)

    torrent_info = PieceManager.create_torrent_from_file(test_file)
    print(f"info_hash: {torrent_info.info_hash.hex()}")
    print(f"num_pieces: {torrent_info.num_pieces}")

    print("\n--- Starting tracker ---")
    tracker = TrackerServer(host='127.0.0.1', port=0)
    tracker.start(daemon=True)
    time.sleep(0.5)
    tracker_port = tracker.server.server_address[1]
    tracker_url = f"http://127.0.0.1:{tracker_port}/announce"
    print(f"Tracker URL: {tracker_url}")

    print("\n--- Testing tracker client ---")
    client = TrackerClient(tracker_url)
    result = client.announce(
        info_hash=torrent_info.info_hash,
        peer_id=b"12345678901234567890",
        port=12345,
        event="started",
        left=0
    )
    print(f"First announce: {result}")

    result2 = client.announce(
        info_hash=torrent_info.info_hash,
        peer_id=b"09876543210987654321",
        port=54321,
        event="started",
        left=1000
    )
    print(f"Second announce: {result2}")

    scrape = client.scrape(torrent_info.info_hash)
    print(f"Scrape: {scrape}")

    print("\n--- Starting seeder ---")
    seeder = Peer(
        host='127.0.0.1',
        port=0,
        download_dir=seeder_dir,
        tracker_url=tracker_url
    )
    seeder.seed_file(test_file)
    seeder.start()
    time.sleep(1)
    seeder_port = seeder.server_sock.getsockname()[1]
    print(f"Seeder port: {seeder_port}")
    print(f"Seeder peers: {seeder.get_num_peers()}")

    print("\n--- Starting leecher ---")
    leecher = Peer(
        host='127.0.0.1',
        port=0,
        download_dir=leecher_dir,
        tracker_url=tracker_url
    )
    leecher.load_torrent(torrent_info, seed=False)
    leecher.start()
    time.sleep(1)
    leecher_port = leecher.server_sock.getsockname()[1]
    print(f"Leecher port: {leecher_port}")
    print(f"Leecher peers: {leecher.get_num_peers()}")

    print("\n--- Waiting for connection ---")
    for i in range(10):
        time.sleep(1)
        print(f"t={i+1}s - Seeder peers: {seeder.get_num_peers()}, Leecher peers: {leecher.get_num_peers()}")
        print(f"  Seeder conns: {list(seeder.connections.keys())}")
        print(f"  Leecher conns: {list(leecher.connections.keys())}")

    print("\n--- Tracker status ---")
    scrape2 = client.scrape(torrent_info.info_hash)
    print(f"Scrape: {scrape2}")

    print("\n--- Direct connect test ---")
    conn = leecher.connect_to_peer('127.0.0.1', seeder_port)
    print(f"Direct connect result: {conn}")
    time.sleep(2)
    print(f"After direct connect - Seeder peers: {seeder.get_num_peers()}, Leecher peers: {leecher.get_num_peers()}")

    print("\n--- Waiting for download ---")
    for i in range(10):
        time.sleep(1)
        progress, total = leecher.get_progress()
        print(f"t={i+1}s - Progress: {progress}/{total}")
        if leecher.is_complete():
            print("Download complete!")
            break

    seeder.stop()
    leecher.stop()
    tracker.stop()


if __name__ == "__main__":
    main()
