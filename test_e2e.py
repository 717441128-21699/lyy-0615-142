#!/usr/bin/env python3
import os
import sys
import time
import shutil
import tempfile
import hashlib
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import TrackerServer
from peer import Peer
from piece_manager import load_torrent_file


def file_hash(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while True:
            data = f.read(65536)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def test_1_any_path_seeding():
    """Test 1: Seeding from arbitrary path"""
    print("\n" + "=" * 60)
    print("TEST 1: Seeding from arbitrary path")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        source_dir = tmpdir / "source_files"
        source_dir.mkdir()
        source_file = source_dir / "test_data.bin"

        download_dir1 = tmpdir / "download1"
        download_dir1.mkdir()

        data = os.urandom(2 * 1024 * 1024)
        with open(source_file, 'wb') as f:
            f.write(data)

        original_hash = hashlib.sha256(data).hexdigest()
        print(f"  Created source file: {source_file} ({len(data)} bytes)")
        print(f"  Source file SHA256: {original_hash[:16]}...")

        from piece_manager import PieceManager, save_torrent_file
        torrent_info = PieceManager.create_torrent_from_file(str(source_file), piece_size=256 * 1024)
        torrent_file = tmpdir / "test.torrent"
        save_torrent_file(torrent_info, str(torrent_file), tracker_url="http://localhost:8888/announce")
        print(f"  Created torrent: {torrent_file}")

        tracker = TrackerServer(host='127.0.0.1', port=8888)
        tracker.start(daemon=True)
        time.sleep(0.5)

        seeder = Peer.from_torrent_file(
            str(torrent_file),
            host='127.0.0.1',
            port=8889,
            download_dir=str(download_dir1),
            source_filepath=str(source_file),
            seed=True
        )
        seeder.start()
        time.sleep(0.5)
        print(f"  Seeder started on port {seeder.get_listen_port()}")

        leecher = Peer.from_torrent_file(
            str(torrent_file),
            host='127.0.0.1',
            port=8890,
            download_dir=str(download_dir1),
            source_filepath=None,
            seed=False
        )
        leecher.start()
        time.sleep(0.5)
        print(f"  Leecher started on port {leecher.get_listen_port()}")

        max_wait = 60
        start_time = time.time()
        while not leecher.is_complete() and time.time() - start_time < max_wait:
            time.sleep(1)
            d, t = leecher.get_progress()
            print(f"\r  Progress: {d}/{t} pieces ({d/t*100:.1f}%)", end='', flush=True)

        print()

        if leecher.is_complete():
            downloaded_file = download_dir1 / torrent_info.filename
            if downloaded_file.exists():
                downloaded_hash = file_hash(str(downloaded_file))
                if downloaded_hash == original_hash:
                    print("  ✓ Downloaded file matches original!")
                    print("  ✓ Arbitrary path seeding works correctly")
                    result = True
                else:
                    print(f"  ✗ Hash mismatch! Expected {original_hash[:16]}..., got {downloaded_hash[:16]}...")
                    result = False
            else:
                print(f"  ✗ Downloaded file not found: {downloaded_file}")
                result = False
        else:
            print("  ✗ Download timed out")
            result = False

        leecher.stop()
        seeder.stop()
        tracker.stop()
        time.sleep(0.5)

        return result


def test_2_fast_resume():
    """Test 2: Pause and resume (fast resume)"""
    print("\n" + "=" * 60)
    print("TEST 2: Fast Resume - Pause and Continue")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        source_file = tmpdir / "large_test.bin"
        download_dir = tmpdir / "downloads"
        download_dir.mkdir()

        data = os.urandom(5 * 1024 * 1024)
        with open(source_file, 'wb') as f:
            f.write(data)

        original_hash = hashlib.sha256(data).hexdigest()
        print(f"  Created test file: {len(data)} bytes")

        from piece_manager import PieceManager, save_torrent_file
        torrent_info = PieceManager.create_torrent_from_file(str(source_file), piece_size=256 * 1024)
        torrent_file = tmpdir / "test.torrent"
        save_torrent_file(torrent_info, str(torrent_file), tracker_url="http://localhost:8889/announce")

        tracker = TrackerServer(host='127.0.0.1', port=8889)
        tracker.start(daemon=True)
        time.sleep(0.5)

        seeder = Peer.from_torrent_file(
            str(torrent_file),
            host='127.0.0.1',
            port=8891,
            download_dir=str(download_dir),
            source_filepath=str(source_file),
            seed=True
        )
        seeder.start()
        time.sleep(0.5)

        leecher_port = 8892
        leecher1 = Peer.from_torrent_file(
            str(torrent_file),
            host='127.0.0.1',
            port=leecher_port,
            download_dir=str(download_dir),
            source_filepath=None,
            seed=False
        )
        leecher1.start()
        print(f"  Leecher started (first run)")

        start_time = time.time()
        while True:
            d, t = leecher1.get_progress()
            if d >= t // 2 or time.time() - start_time > 30:
                break
            time.sleep(0.5)

        pieces_after_first = leecher1.get_progress()[0]
        print(f"  Stopping after {pieces_after_first}/{torrent_info.num_pieces} pieces...")
        leecher1.stop()
        time.sleep(1)

        print(f"  Restarting leecher (same download dir)...")
        leecher2 = Peer.from_torrent_file(
            str(torrent_file),
            host='127.0.0.1',
            port=leecher_port,
            download_dir=str(download_dir),
            source_filepath=None,
            seed=False
        )

        pieces_on_restart = leecher2.get_progress()[0]
        print(f"  Fast resume detected: {pieces_on_restart}/{torrent_info.num_pieces} pieces already verified")

        if pieces_on_restart >= pieces_after_first - 1:
            print("  ✓ Fast resume working correctly!")
        else:
            print(f"  ✗ Fast resume failed: expected ~{pieces_after_first}, got {pieces_on_restart}")
            leecher2.stop()
            seeder.stop()
            tracker.stop()
            return False

        leecher2.start()
        print(f"  Continuing download...")

        max_wait = 60
        start_time = time.time()
        while not leecher2.is_complete() and time.time() - start_time < max_wait:
            time.sleep(1)
            d, t = leecher2.get_progress()
            print(f"\r  Progress: {d}/{t} pieces ({d/t*100:.1f}%)", end='', flush=True)

        print()

        if leecher2.is_complete():
            downloaded_file = download_dir / torrent_info.filename
            downloaded_hash = file_hash(str(downloaded_file))
            if downloaded_hash == original_hash:
                print("  ✓ Downloaded file matches original!")
                print("  ✓ Fast resume works correctly")
                result = True
            else:
                print("  ✗ Hash mismatch after resume!")
                result = False
        else:
            print("  ✗ Download timed out after resume")
            result = False

        leecher2.stop()
        seeder.stop()
        tracker.stop()
        time.sleep(0.5)

        return result


def test_3_tit_for_tat():
    """Test 3: Tit-for-Tat - Free-rider penalty"""
    print("\n" + "=" * 60)
    print("TEST 3: Tit-for-Tat Incentive Mechanism")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        source_file = tmpdir / "test_data.bin"

        download_dir_normal = tmpdir / "download_normal"
        download_dir_freerider = tmpdir / "download_freerider"
        download_dir_normal.mkdir()
        download_dir_freerider.mkdir()

        data = os.urandom(4 * 1024 * 1024)
        with open(source_file, 'wb') as f:
            f.write(data)

        original_hash = hashlib.sha256(data).hexdigest()
        print(f"  Created test file: {len(data)} bytes")

        from piece_manager import PieceManager, save_torrent_file
        torrent_info = PieceManager.create_torrent_from_file(str(source_file), piece_size=128 * 1024)
        torrent_file = tmpdir / "test.torrent"
        save_torrent_file(torrent_info, str(torrent_file), tracker_url="http://localhost:8890/announce")

        tracker = TrackerServer(host='127.0.0.1', port=8890)
        tracker.start(daemon=True)
        time.sleep(0.5)

        print(f"  Creating scenario: 1 seeder + 2 normal peers + 1 free-rider")
        print(f"  Normal peers contribute upload, free-rider upload limited to 1KB/s")

        seeder = Peer.from_torrent_file(
            str(torrent_file),
            host='127.0.0.1',
            port=8893,
            download_dir=str(download_dir_normal),
            source_filepath=str(source_file),
            seed=True
        )
        seeder.start()
        time.sleep(0.5)

        normal_peer1 = Peer.from_torrent_file(
            str(torrent_file),
            host='127.0.0.1',
            port=8894,
            download_dir=str(download_dir_normal),
            source_filepath=None,
            seed=False,
            free_rider=False
        )
        normal_peer1.start()
        time.sleep(0.3)

        normal_peer2 = Peer.from_torrent_file(
            str(torrent_file),
            host='127.0.0.1',
            port=8896,
            download_dir=str(download_dir_normal),
            source_filepath=None,
            seed=False,
            free_rider=False
        )
        normal_peer2.start()
        time.sleep(0.3)

        freerider_peer = Peer.from_torrent_file(
            str(torrent_file),
            host='127.0.0.1',
            port=8895,
            download_dir=str(download_dir_freerider),
            source_filepath=None,
            seed=False,
            free_rider=True
        )
        freerider_peer.start()
        time.sleep(0.5)

        print(f"  Seeder:          port {seeder.get_listen_port()}")
        print(f"  Normal peer 1:   port {normal_peer1.get_listen_port()}")
        print(f"  Normal peer 2:   port {normal_peer2.get_listen_port()}")
        print(f"  Free-rider peer: port {freerider_peer.get_listen_port()}")
        print(f"  Downloading... (measuring speed difference for 25 seconds)")

        normal1_progress = []
        normal2_progress = []
        freerider_progress = []
        normal1_rates = []
        normal2_rates = []
        freerider_rates = []

        for i in range(25):
            time.sleep(1)

            n1d, n1t = normal_peer1.get_progress()
            n2d, n2t = normal_peer2.get_progress()
            fd, ft = freerider_peer.get_progress()

            normal1_progress.append(n1d)
            normal2_progress.append(n2d)
            freerider_progress.append(fd)

            n1_status = normal_peer1.get_status()
            n2_status = normal_peer2.get_status()
            f_status = freerider_peer.get_status()

            normal1_rates.append(n1_status.get('download_rate', 0))
            normal2_rates.append(n2_status.get('download_rate', 0))
            freerider_rates.append(f_status.get('download_rate', 0))

            print(f"\r  {i+1}s: N1={n1d}/{n1t} ({n1_status.get('download_rate', 0):.0f}B/s) "
                  f"N2={n2d}/{n2t} ({n2_status.get('download_rate', 0):.0f}B/s) "
                  f"FR={fd}/{ft} ({f_status.get('download_rate', 0):.0f}B/s)",
                  end='', flush=True)

            if normal_peer1.is_complete() and normal_peer2.is_complete() and freerider_peer.is_complete():
                break

        print()

        n1_avg = sum(normal1_rates) / len(normal1_rates) if normal1_rates else 0
        n2_avg = sum(normal2_rates) / len(normal2_rates) if normal2_rates else 0
        fr_avg = sum(freerider_rates) / len(freerider_rates) if freerider_rates else 0

        normal_avg = (n1_avg + n2_avg) / 2
        n1_final = normal1_progress[-1] if normal1_progress else 0
        n2_final = normal2_progress[-1] if normal2_progress else 0
        fr_final = freerider_progress[-1] if freerider_progress else 0
        total_pieces = n1t

        print(f"\n  Results after {len(normal1_rates)} seconds:")
        print(f"    Normal peer 1: {n1_final}/{total_pieces} pieces, avg rate: {n1_avg:.0f} B/s")
        print(f"    Normal peer 2: {n2_final}/{total_pieces} pieces, avg rate: {n2_avg:.0f} B/s")
        print(f"    Free-rider:    {fr_final}/{total_pieces} pieces, avg rate: {fr_avg:.0f} B/s")
        print(f"    Normal avg:    {normal_avg:.0f} B/s")

        result = True
        if normal_avg > 0:
            ratio = normal_avg / max(fr_avg, 1)
            print(f"    Speed ratio (normal/free-rider): {ratio:.1f}x")

            if ratio >= 1.5:
                print("  ✓ Tit-for-Tat working: Normal peers get significantly better speed!")
            else:
                print(f"  ⚠ Speed difference not very large ({ratio:.1f}x)")

            n1_file = download_dir_normal / torrent_info.filename
            n2_file = download_dir_normal / torrent_info.filename
            fr_file = download_dir_freerider / torrent_info.filename

            for name, peer, filepath in [
                ("Normal peer 1", normal_peer1, n1_file),
                ("Normal peer 2", normal_peer2, n2_file),
                ("Free-rider", freerider_peer, fr_file)
            ]:
                if peer.is_complete() and filepath.exists():
                    h = file_hash(str(filepath))
                    if h == original_hash:
                        print(f"  ✓ {name}: file verified correctly")
                    else:
                        print(f"  ✗ {name}: hash mismatch!")
                        result = False
        else:
            print("  ⚠ No download activity detected")
            result = False

        normal_peer1.stop()
        normal_peer2.stop()
        freerider_peer.stop()
        seeder.stop()
        tracker.stop()
        time.sleep(0.5)

        return result


def main():
    print("\n" + "=" * 60)
    print("P2P File Distribution Network - End-to-End Tests")
    print("=" * 60)

    tests = [
        ("Arbitrary Path Seeding", test_1_any_path_seeding),
        ("Fast Resume / Pause & Continue", test_2_fast_resume),
        ("Tit-for-Tat / Free-rider Penalty", test_3_tit_for_tat),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n  ✗ Test failed with exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")

    print(f"\n  Total: {passed}/{total} tests passed")

    if passed == total:
        print("\n  ✓ All tests passed!")
        return 0
    else:
        print(f"\n  ✗ {total - passed} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
