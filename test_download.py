import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import TrackerServer
from peer import Peer
from piece_manager import PieceManager


def main():
    print("=" * 50)
    print("Simple P2P Download Test")
    print("=" * 50)

    base_dir = tempfile.mkdtemp(prefix="p2p_test_")
    seeder_dir = os.path.join(base_dir, "seeder")
    leecher_dir = os.path.join(base_dir, "leecher")
    os.makedirs(seeder_dir)
    os.makedirs(leecher_dir)

    file_size = 512 * 1024  # 512KB
    test_filename = "testfile.bin"
    test_filepath = os.path.join(seeder_dir, test_filename)

    with open(test_filepath, 'wb') as f:
        f.write(b"A" * file_size)
    print(f"Created test file: {file_size} bytes")

    torrent_info = PieceManager.create_torrent_from_file(test_filepath)
    print(f"Info hash: {torrent_info.info_hash.hex()[:16]}...")
    print(f"Num pieces: {torrent_info.num_pieces}")

    print("\nStarting tracker...")
    tracker = TrackerServer(host='127.0.0.1', port=0)
    tracker.start(daemon=True)
    time.sleep(0.3)
    tracker_port = tracker.server.server_address[1]
    tracker_url = f"http://127.0.0.1:{tracker_port}/announce"
    print(f"Tracker port: {tracker_port}")

    print("\nStarting seeder...")
    seeder = Peer(
        host='127.0.0.1',
        port=0,
        download_dir=seeder_dir,
        tracker_url=tracker_url
    )
    seeder.seed_file(test_filepath)
    seeder.start()
    time.sleep(0.5)
    seeder_port = seeder.get_listen_port()
    print(f"Seeder port: {seeder_port}")

    print("\nStarting leecher...")
    leecher = Peer(
        host='127.0.0.1',
        port=0,
        download_dir=leecher_dir,
        tracker_url=tracker_url
    )
    leecher.load_torrent(torrent_info, seed=False)
    leecher.start()
    time.sleep(0.5)
    leecher_port = leecher.get_listen_port()
    print(f"Leecher port: {leecher_port}")

    print("\nWaiting for download...")
    start_time = time.time()
    max_time = 60

    while time.time() - start_time < max_time:
        time.sleep(2)
        progress, total = leecher.get_progress()
        elapsed = time.time() - start_time
        seeder_peers = seeder.get_num_peers()
        leecher_peers = leecher.get_num_peers()

        print(f"\n[{elapsed:5.1f}s] Progress: {progress}/{total} pieces")
        print(f"  Seeder peers: {seeder_peers}, Leecher peers: {leecher_peers}")
        print(f"  Leecher down: {leecher.download_total}B, up: {leecher.upload_total}B")

        seeder_stats = seeder.get_peer_stats()
        for pid, s in seeder_stats.items():
            print(f"  Seeder -> {pid}: peer_interested={s['interested']}, am_choking={s['choked']}")

        leecher_stats = leecher.get_peer_stats()
        for pid, s in leecher_stats.items():
            print(f"  Leecher -> {pid}: am_interested={s['interesting']}, peer_choking={s['choking']}")

        if leecher.is_complete():
            print(f"\nDownload complete in {elapsed:.1f} seconds!")
            break

    print("\nVerifying file...")
    dl_path = os.path.join(leecher_dir, test_filename)
    if os.path.exists(dl_path):
        import hashlib
        with open(test_filepath, 'rb') as f:
            orig_hash = hashlib.sha1(f.read()).hexdigest()
        with open(dl_path, 'rb') as f:
            dl_hash = hashlib.sha1(f.read()).hexdigest()
        print(f"  Original: {orig_hash}")
        print(f"  Download: {dl_hash}")
        print(f"  Match: {orig_hash == dl_hash}")
    else:
        print("  File not found!")

    print("\nPeer stats:")
    stats = leecher.get_peer_stats()
    for pid, s in stats.items():
        print(f"  {pid}: up={s['up']} down={s['down']} "
              f"choking={s['choking']} choked={s['choked']} "
              f"interested={s['interested']} interesting={s['interesting']}")

    print("\nCleaning up...")
    leecher.stop()
    seeder.stop()
    tracker.stop()

    print(f"\nTest files in: {base_dir}")
    print("Done!")


if __name__ == "__main__":
    main()
