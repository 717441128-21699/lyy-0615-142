#!/usr/bin/env python3
import os
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import TrackerServer, TrackerClient
from peer import Peer
from piece_manager import PieceManager, save_torrent_file, load_torrent_file


def test():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        source_file = tmpdir / "test.bin"
        data = b"Hello World! " * 10000
        with open(source_file, 'wb') as f:
            f.write(data)

        torrent_info = PieceManager.create_torrent_from_file(str(source_file), piece_size=64 * 1024)
        torrent_file = tmpdir / "test.torrent"
        tracker_url = "http://127.0.0.1:9999/announce"
        save_torrent_file(torrent_info, str(torrent_file), tracker_url=tracker_url)

        print("Starting tracker...")
        tracker = TrackerServer(host='127.0.0.1', port=9999)
        tracker.start(daemon=True)
        time.sleep(0.5)

        print("Testing tracker client...")
        client = TrackerClient(tracker_url)
        result = client.announce(
            info_hash=torrent_info.info_hash,
            peer_id=b'-TEST0001-123456789012',
            port=9998,
            event="started",
            left=0
        )
        print(f"Tracker announce result: {result}")

        result2 = client.announce(
            info_hash=torrent_info.info_hash,
            peer_id=b'-TEST0002-123456789012',
            port=9997,
            event="started",
            left=1000
        )
        print(f"Tracker announce result2: {result2}")

        print("\nStarting seeder...")
        download_dir = tmpdir / "dl"
        download_dir.mkdir()

        seeder = Peer.from_torrent_file(
            str(torrent_file),
            host='127.0.0.1',
            port=9998,
            download_dir=str(download_dir),
            source_filepath=str(source_file),
            seed=True
        )
        print(f"Seeder torrent_info: {seeder.torrent_info is not None}")
        print(f"Seeder piece_manager: {seeder.piece_manager is not None}")
        print(f"Seeder complete: {seeder.is_complete()}")
        seeder.start()
        print(f"Seeder port: {seeder.get_listen_port()}")
        time.sleep(1)

        print("\nStarting leecher...")
        leecher = Peer.from_torrent_file(
            str(torrent_file),
            host='127.0.0.1',
            port=9997,
            download_dir=str(download_dir),
            source_filepath=None,
            seed=False
        )
        print(f"Leecher torrent_info: {leecher.torrent_info is not None}")
        print(f"Leecher piece_manager: {leecher.piece_manager is not None}")
        leecher.start()
        print(f"Leecher port: {leecher.get_listen_port()}")

        print("\nManual test: leecher connect to seeder...")
        conn = leecher.connect_to_peer('127.0.0.1', seeder.get_listen_port())
        print(f"Connection result: {conn}")
        time.sleep(2)

        print(f"\nLeecher peers: {leecher.get_num_peers()}")
        print(f"Seeder peers: {seeder.get_num_peers()}")

        leecher_status = leecher.get_status()
        print(f"\nLeecher status: {leecher_status}")

        print("\nWaiting for download...")
        for i in range(10):
            time.sleep(1)
            d, t = leecher.get_progress()
            print(f"  {i}s: {d}/{t} pieces, peers={leecher.get_num_peers()}")
            if leecher.is_complete():
                print("Download complete!")
                break

        leecher.stop()
        seeder.stop()
        tracker.stop()


if __name__ == '__main__':
    test()
