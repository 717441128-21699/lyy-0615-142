import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from peer import Peer
from piece_manager import PieceManager


def main():
    print("=" * 50)
    print("Direct Connect Download Test")
    print("=" * 50)

    base_dir = tempfile.mkdtemp(prefix="p2p_direct_")
    seeder_dir = os.path.join(base_dir, "seeder")
    leecher_dir = os.path.join(base_dir, "leecher")
    os.makedirs(seeder_dir)
    os.makedirs(leecher_dir)

    file_size = 256 * 1024  # 256KB = 1 piece
    test_filename = "testfile.bin"
    test_filepath = os.path.join(seeder_dir, test_filename)

    with open(test_filepath, 'wb') as f:
        f.write(b"A" * file_size)
    print(f"Created test file: {file_size} bytes")

    torrent_info = PieceManager.create_torrent_from_file(test_filepath)
    print(f"Info hash: {torrent_info.info_hash.hex()[:16]}...")
    print(f"Num pieces: {torrent_info.num_pieces}")

    print("\nStarting seeder (no tracker)...")
    seeder = Peer(
        host='127.0.0.1',
        port=0,
        download_dir=seeder_dir,
        tracker_url=None
    )
    seeder.seed_file(test_filepath)
    seeder.start()
    time.sleep(0.5)
    seeder_port = seeder.get_listen_port()
    print(f"Seeder port: {seeder_port}")
    print(f"Seeder pieces: {seeder.piece_manager.bitfield.count_set()}/{seeder.piece_manager.num_pieces}")

    print("\nStarting leecher (no tracker)...")
    leecher = Peer(
        host='127.0.0.1',
        port=0,
        download_dir=leecher_dir,
        tracker_url=None
    )
    leecher.load_torrent(torrent_info, seed=False)
    leecher.start()
    time.sleep(0.5)
    leecher_port = leecher.get_listen_port()
    print(f"Leecher port: {leecher_port}")

    print("\nManually connecting leecher to seeder...")
    conn = leecher.connect_to_peer('127.0.0.1', seeder_port)
    print(f"Connection result: {conn}")
    time.sleep(2)

    print(f"\nSeeder peers: {seeder.get_num_peers()}")
    print(f"Leecher peers: {leecher.get_num_peers()}")

    print("\nPeer stats (leecher -> seeder):")
    stats = leecher.get_peer_stats()
    for pid, s in stats.items():
        print(f"  {pid}: am_interested={s['interesting']}, peer_choking={s['choking']}, "
              f"peer_interested={s['interested']}, am_choking={s['choked']}")

    print("\nPeer stats (seeder -> leecher):")
    stats = seeder.get_peer_stats()
    for pid, s in stats.items():
        print(f"  {pid}: am_interested={s['interesting']}, peer_choking={s['choking']}, "
              f"peer_interested={s['interested']}, am_choking={s['choked']}")

    print("\nWaiting for download...")
    start_time = time.time()
    for i in range(30):
        time.sleep(1)
        progress, total = leecher.get_progress()
        elapsed = time.time() - start_time
        print(f"  [{elapsed:4.1f}s] Progress: {progress}/{total} | "
              f"down={leecher.download_total}B up={leecher.upload_total}B | "
              f"seeder_up={seeder.upload_total}B")

        if leecher.is_complete():
            print(f"\nDownload complete in {elapsed:.1f}s!")
            break

    print("\nFinal peer stats (leecher):")
    stats = leecher.get_peer_stats()
    for pid, s in stats.items():
        print(f"  {pid}: {s}")

    print("\nVerifying file...")
    dl_path = os.path.join(leecher_dir, test_filename)
    if os.path.exists(dl_path):
        import hashlib
        with open(test_filepath, 'rb') as f:
            orig = hashlib.sha1(f.read()).hexdigest()
        with open(dl_path, 'rb') as f:
            dl = hashlib.sha1(f.read()).hexdigest()
        print(f"  Original: {orig}")
        print(f"  Download: {dl}")
        print(f"  Match: {orig == dl}")
    else:
        print("  File not found!")

    seeder.stop()
    leecher.stop()
    print(f"\nTest files: {base_dir}")
    print("Done.")


if __name__ == "__main__":
    main()
