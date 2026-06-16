import os
import sys
import time
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import TrackerServer
from peer import Peer
from piece_manager import PieceManager


def create_test_file(filepath: str, size: int):
    with open(filepath, 'wb') as f:
        chunk = b"HelloP2PWorld!" * 1000
        remaining = size
        while remaining > 0:
            write_size = min(len(chunk), remaining)
            f.write(chunk[:write_size])
            remaining -= write_size


def main():
    print("=" * 60)
    print("P2P File Distribution Network - Demo")
    print("=" * 60)

    base_dir = tempfile.mkdtemp(prefix="p2p_demo_")
    print(f"\nWorking directory: {base_dir}")

    seeder_dir = os.path.join(base_dir, "seeder")
    leecher1_dir = os.path.join(base_dir, "leecher1")
    leecher2_dir = os.path.join(base_dir, "leecher2")
    leecher3_dir = os.path.join(base_dir, "leecher3")

    for d in [seeder_dir, leecher1_dir, leecher2_dir, leecher3_dir]:
        os.makedirs(d, exist_ok=True)

    file_size = 2 * 1024 * 1024  # 2MB
    test_filename = "testfile.bin"
    test_filepath = os.path.join(seeder_dir, test_filename)
    create_test_file(test_filepath, file_size)
    print(f"\nCreated test file: {test_filename} ({file_size} bytes)")

    torrent_info = PieceManager.create_torrent_from_file(test_filepath)
    print(f"Torrent info_hash: {torrent_info.info_hash.hex()[:16]}...")
    print(f"Total pieces: {torrent_info.num_pieces}")
    print(f"Piece size: {torrent_info.piece_size} bytes")

    print("\n" + "=" * 60)
    print("Starting Tracker...")
    tracker = TrackerServer(host='127.0.0.1', port=0)
    tracker.start(daemon=True)
    time.sleep(0.5)
    tracker_port = tracker.server.server_address[1]
    tracker_url = f"http://127.0.0.1:{tracker_port}/announce"
    print(f"Tracker URL: {tracker_url}")

    print("\n" + "=" * 60)
    print("Starting Seeder (has complete file)...")
    seeder = Peer(
        host='127.0.0.1',
        port=0,
        download_dir=seeder_dir,
        tracker_url=tracker_url
    )
    seeder.seed_file(test_filepath)
    seeder.seed_after_complete = True
    seeder.start()
    time.sleep(0.5)
    seeder_port = seeder.server_sock.getsockname()[1]
    print(f"Seeder ID: {seeder.peer_id.hex()[:8]}... port: {seeder_port}")

    print("\n" + "=" * 60)
    print("Starting Leechers (downloading)...")

    leechers = []
    for i, leecher_dir in enumerate([leecher1_dir, leecher2_dir, leecher3_dir]):
        leecher = Peer(
            host='127.0.0.1',
            port=0,
            download_dir=leecher_dir,
            tracker_url=tracker_url
        )
        leecher.load_torrent(torrent_info, seed=False)
        leecher.seed_after_complete = True

        def make_progress_cb(idx):
            def cb(d, t):
                pass
            return cb

        leecher.progress_callback = make_progress_cb(i)
        leecher.start()
        leechers.append((leecher, leecher_dir))
        time.sleep(0.3)
        port = leecher.server_sock.getsockname()[1]
        print(f"  Leecher {i+1}: {leecher.peer_id.hex()[:8]}... port: {port}")

    print("\n" + "=" * 60)
    print("Watching download progress...")
    print("=" * 60)

    start_time = time.time()
    max_wait = 120
    all_complete = False

    try:
        while time.time() - start_time < max_wait:
            time.sleep(1)

            status_lines = []
            all_done = True

            seeder_progress, seeder_total = seeder.get_progress()
            status_lines.append(
                f"  [Seeder  ] {seeder_progress}/{seeder_total} pieces "
                f"| peers: {seeder.get_num_peers()} "
                f"| up: {seeder.upload_total // 1024}KB"
            )

            for i, (leecher, _) in enumerate(leechers):
                progress, total = leecher.get_progress()
                complete = leecher.is_complete()
                if not complete:
                    all_done = False
                status = "DONE" if complete else "DOWN"
                status_lines.append(
                    f"  [Leecher{i+1}] {progress}/{total} pieces "
                    f"| peers: {leecher.get_num_peers()} "
                    f"| down: {leecher.download_total // 1024}KB "
                    f"| up: {leecher.upload_total // 1024}KB "
                    f"[{status}]"
                )

            os.system('cls' if os.name == 'nt' else 'clear')
            print("=" * 60)
            print("P2P Download Progress")
            print(f"Time elapsed: {int(time.time() - start_time)}s")
            print("=" * 60)
            for line in status_lines:
                print(line)
            print("=" * 60)

            if all_done:
                all_complete = True
                elapsed = time.time() - start_time
                print(f"\nAll leechers completed download in {elapsed:.1f} seconds!")
                break

    except KeyboardInterrupt:
        print("\nInterrupted by user")

    print("\n" + "=" * 60)
    print("Verifying downloaded files...")
    print("=" * 60)

    import hashlib
    with open(test_filepath, 'rb') as f:
        original_hash = hashlib.sha1(f.read()).hexdigest()
    print(f"Original file SHA1: {original_hash}")

    all_match = True
    for i, (leecher, leecher_dir) in enumerate(leechers):
        dl_path = os.path.join(leecher_dir, test_filename)
        if os.path.exists(dl_path):
            with open(dl_path, 'rb') as f:
                dl_hash = hashlib.sha1(f.read()).hexdigest()
            match = "✓ OK" if dl_hash == original_hash else "✗ MISMATCH"
            if dl_hash != original_hash:
                all_match = False
            print(f"  Leecher {i+1}: {dl_hash} {match}")
        else:
            print(f"  Leecher {i+1}: File not found")
            all_match = False

    print("\n" + "=" * 60)
    if all_match and all_complete:
        print("✓ SUCCESS: All downloads verified correctly!")
    else:
        print("✗ FAILURE: Some downloads failed verification")
    print("=" * 60)

    print("\nCleaning up...")
    seeder.stop()
    for leecher, _ in leechers:
        leecher.stop()
    tracker.stop()

    print(f"Test files in: {base_dir}")
    print("\nDone!")


if __name__ == "__main__":
    main()
