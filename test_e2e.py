#!/usr/bin/env python3
import os
import sys
import time
import tempfile
import hashlib
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import TrackerServer
from peer import Peer, DEFAULT_UPLOAD_LIMIT, FREE_RIDER_UPLOAD_LIMIT
from piece_manager import PieceManager, save_torrent_file, load_torrent_file


def file_hash(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while True:
            data = f.read(65536)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    else:
        return f"{n / (1024 * 1024):.1f} MB"


def format_rate(rate: float) -> str:
    return f"{format_size(int(rate))}/s"


def test_1_any_path_seeding():
    print("\n" + "=" * 60)
    print("TEST 1: Seeding from arbitrary path")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        source_dir = tmpdir / "source_files"
        source_dir.mkdir()
        source_file = source_dir / "test_data.bin"

        download_dir = tmpdir / "download"
        download_dir.mkdir()

        data = os.urandom(2 * 1024 * 1024)
        with open(source_file, 'wb') as f:
            f.write(data)

        original_hash = hashlib.sha256(data).hexdigest()
        print(f"  Source: {source_file} ({len(data)} bytes)")

        torrent_info = PieceManager.create_torrent_from_file(str(source_file), piece_size=256 * 1024)
        torrent_file = tmpdir / "test.torrent"
        save_torrent_file(torrent_info, str(torrent_file), tracker_url="http://localhost:30001/announce")

        tracker = TrackerServer(host='127.0.0.1', port=30001)
        tracker.start(daemon=True)
        time.sleep(0.3)

        seeder = Peer.from_torrent_file(
            str(torrent_file), host='127.0.0.1', port=30002,
            download_dir=str(download_dir),
            source_filepath=str(source_file), seed=True,
            upload_limit=DEFAULT_UPLOAD_LIMIT
        )
        seeder.start()
        time.sleep(0.3)

        leecher = Peer.from_torrent_file(
            str(torrent_file), host='127.0.0.1', port=30003,
            download_dir=str(download_dir), source_filepath=None, seed=False
        )
        leecher.start()
        time.sleep(0.3)

        start_time = time.time()
        while not leecher.is_complete() and time.time() - start_time < 60:
            time.sleep(0.5)
            d, t = leecher.get_progress()
            print(f"\r  Progress: {d}/{t} pieces ({d/t*100:.1f}%)", end='', flush=True)
        print()

        result = False
        if leecher.is_complete():
            downloaded_file = download_dir / torrent_info.filename
            if downloaded_file.exists():
                downloaded_hash = file_hash(str(downloaded_file))
                if downloaded_hash == original_hash:
                    print("  ✓ Arbitrary path seeding works correctly")
                    result = True
                else:
                    print(f"  ✗ Hash mismatch!")
            else:
                print(f"  ✗ Downloaded file not found")
        else:
            print("  ✗ Download timed out")

        leecher.stop()
        seeder.stop()
        tracker.stop()
        time.sleep(0.3)
        return result


def test_2_fast_resume():
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

        torrent_info = PieceManager.create_torrent_from_file(str(source_file), piece_size=256 * 1024)
        torrent_file = tmpdir / "test.torrent"
        save_torrent_file(torrent_info, str(torrent_file), tracker_url="http://localhost:30011/announce")

        tracker = TrackerServer(host='127.0.0.1', port=30011)
        tracker.start(daemon=True)
        time.sleep(0.3)

        seeder = Peer.from_torrent_file(
            str(torrent_file), host='127.0.0.1', port=30012,
            download_dir=str(download_dir),
            source_filepath=str(source_file), seed=True,
            upload_limit=DEFAULT_UPLOAD_LIMIT
        )
        seeder.start()
        time.sleep(0.3)

        leecher1 = Peer.from_torrent_file(
            str(torrent_file), host='127.0.0.1', port=30013,
            download_dir=str(download_dir), source_filepath=None, seed=False
        )
        leecher1.start()
        print(f"  Leecher started (first run)")

        start_time = time.time()
        while True:
            d, t = leecher1.get_progress()
            if d >= t // 2 or time.time() - start_time > 60:
                break
            time.sleep(0.5)

        pieces_after_first = leecher1.get_progress()[0]
        print(f"  Stopping after {pieces_after_first}/{torrent_info.num_pieces} pieces...")
        leecher1.stop()
        time.sleep(0.5)

        print(f"  Restarting leecher (same download dir)...")
        leecher2 = Peer.from_torrent_file(
            str(torrent_file), host='127.0.0.1', port=30013,
            download_dir=str(download_dir), source_filepath=None, seed=False
        )

        pieces_on_restart = leecher2.get_progress()[0]
        print(f"  Fast resume: {pieces_on_restart}/{torrent_info.num_pieces} pieces already verified")

        if pieces_on_restart < pieces_after_first - 1:
            print(f"  ✗ Fast resume failed: expected ~{pieces_after_first}, got {pieces_on_restart}")
            seeder.stop()
            tracker.stop()
            return False

        print("  ✓ Fast resume working correctly!")
        leecher2.start()

        start_time = time.time()
        while not leecher2.is_complete() and time.time() - start_time < 60:
            time.sleep(0.5)
            d, t = leecher2.get_progress()
            print(f"\r  Progress: {d}/{t} pieces ({d/t*100:.1f}%)", end='', flush=True)
        print()

        result = False
        if leecher2.is_complete():
            downloaded_file = download_dir / torrent_info.filename
            downloaded_hash = file_hash(str(downloaded_file))
            if downloaded_hash == original_hash:
                print("  ✓ Fast resume works correctly")
                result = True
            else:
                print("  ✗ Hash mismatch after resume!")
        else:
            print("  ✗ Download timed out after resume")

        leecher2.stop()
        seeder.stop()
        tracker.stop()
        time.sleep(0.3)
        return result


def test_3_tit_for_tat():
    print("\n" + "=" * 60)
    print("TEST 3: Tit-for-Tat / Free-rider Penalty")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        source_file = tmpdir / "test_data.bin"

        dl_n1 = tmpdir / "dl_normal1"
        dl_n2 = tmpdir / "dl_normal2"
        dl_fr = tmpdir / "dl_freerider"
        dl_n1.mkdir()
        dl_n2.mkdir()
        dl_fr.mkdir()

        data = os.urandom(8 * 1024 * 1024)
        with open(source_file, 'wb') as f:
            f.write(data)

        original_hash = hashlib.sha256(data).hexdigest()
        print(f"  File: {len(data)} bytes")
        print(f"  Scenario: 1 seeder + 2 normal peers + 1 free-rider")

        upload_cap = 100 * 1024
        print(f"  Upload cap per peer: {format_size(upload_cap)}/s")
        print(f"  Free-rider upload limit: {format_size(FREE_RIDER_UPLOAD_LIMIT)}/s")

        torrent_info = PieceManager.create_torrent_from_file(str(source_file), piece_size=128 * 1024)
        torrent_file = tmpdir / "test.torrent"
        save_torrent_file(torrent_info, str(torrent_file), tracker_url="http://localhost:30021/announce")

        tracker = TrackerServer(host='127.0.0.1', port=30021)
        tracker.start(daemon=True)
        time.sleep(0.3)

        seeder = Peer.from_torrent_file(
            str(torrent_file), host='127.0.0.1', port=30022,
            download_dir=str(dl_n1),
            source_filepath=str(source_file), seed=True,
            upload_limit=upload_cap
        )
        seeder.start()
        time.sleep(0.3)

        normal1 = Peer.from_torrent_file(
            str(torrent_file), host='127.0.0.1', port=30023,
            download_dir=str(dl_n1), source_filepath=None, seed=False,
            free_rider=False, upload_limit=upload_cap
        )
        normal1.start()
        time.sleep(0.2)

        normal2 = Peer.from_torrent_file(
            str(torrent_file), host='127.0.0.1', port=30024,
            download_dir=str(dl_n2), source_filepath=None, seed=False,
            free_rider=False, upload_limit=upload_cap
        )
        normal2.start()
        time.sleep(0.2)

        freerider = Peer.from_torrent_file(
            str(torrent_file), host='127.0.0.1', port=30025,
            download_dir=str(dl_fr), source_filepath=None, seed=False,
            free_rider=True, upload_limit=upload_cap
        )
        freerider.start()
        time.sleep(0.5)

        print(f"  Seeder:       port {seeder.get_listen_port()}")
        print(f"  Normal 1:     port {normal1.get_listen_port()}")
        print(f"  Normal 2:     port {normal2.get_listen_port()}")
        print(f"  Free-rider:   port {freerider.get_listen_port()}")
        print()

        measure_secs = 30
        normal1_rates = []
        normal2_rates = []
        fr_rates = []
        n1_progress = []
        n2_progress = []
        fr_progress = []

        for i in range(measure_secs):
            time.sleep(1)

            n1s = normal1.get_status()
            n2s = normal2.get_status()
            frs = freerider.get_status()

            n1r = n1s.get('download_rate', 0)
            n2r = n2s.get('download_rate', 0)
            frr = frs.get('download_rate', 0)

            normal1_rates.append(n1r)
            normal2_rates.append(n2r)
            fr_rates.append(frr)

            n1d, n1t = normal1.get_progress()
            n2d, n2t = normal2.get_progress()
            frd, frt = freerider.get_progress()
            n1_progress.append(n1d)
            n2_progress.append(n2d)
            fr_progress.append(frd)

            print(f"\r  {i+1:2d}s | N1: {n1d:2d}/{n1t} {format_rate(n1r):>12s} "
                  f"| N2: {n2d:2d}/{n2t} {format_rate(n2r):>12s} "
                  f"| FR: {frd:2d}/{frt} {format_rate(frr):>12s}", end='', flush=True)

            if normal1.is_complete() and normal2.is_complete() and freerider.is_complete():
                break

        print()

        n1_avg = sum(normal1_rates) / len(normal1_rates) if normal1_rates else 0
        n2_avg = sum(normal2_rates) / len(normal2_rates) if normal2_rates else 0
        fr_avg = sum(fr_rates) / len(fr_rates) if fr_rates else 0
        normal_avg = (n1_avg + n2_avg) / 2

        n1_final = n1_progress[-1] if n1_progress else 0
        n2_final = n2_progress[-1] if n2_progress else 0
        fr_final = fr_progress[-1] if fr_progress else 0
        total_pieces = torrent_info.num_pieces

        print(f"\n  Results:")
        print(f"    Normal 1:    {n1_final}/{total_pieces} pieces, avg rate: {format_rate(n1_avg)}")
        print(f"    Normal 2:    {n2_final}/{total_pieces} pieces, avg rate: {format_rate(n2_avg)}")
        print(f"    Free-rider:  {fr_final}/{total_pieces} pieces, avg rate: {format_rate(fr_avg)}")
        print(f"    Normal avg:  {format_rate(normal_avg)}")

        result = True

        if normal_avg <= 0:
            print("  ✗ FAIL: No download activity on normal peers")
            result = False
        else:
            ratio = normal_avg / max(fr_avg, 1)
            print(f"    Speed ratio (normal/fr): {ratio:.1f}x")

            normal_pieces_avg = (n1_final + n2_final) / 2
            pieces_ratio = normal_pieces_avg / max(fr_final, 1)
            print(f"    Pieces ratio (normal/fr): {pieces_ratio:.1f}x")

            if ratio < 1.5 and pieces_ratio < 1.3:
                print("  ✗ FAIL: Speed difference not significant (ratio < 1.5x, pieces ratio < 1.3x)")
                result = False
            elif ratio < 1.5:
                print(f"  ⚠ WARN: Speed ratio only {ratio:.1f}x, but pieces ratio {pieces_ratio:.1f}x is OK")
            else:
                print("  ✓ PASS: Normal peers significantly faster than free-rider")

        for name, peer, dl_dir in [
            ("Normal 1", normal1, dl_n1),
            ("Normal 2", normal2, dl_n2),
            ("Free-rider", freerider, dl_fr),
        ]:
            filepath = dl_dir / torrent_info.filename
            if peer.is_complete() and filepath.exists():
                h = file_hash(str(filepath))
                if h == original_hash:
                    print(f"  ✓ {name}: file verified")
                else:
                    print(f"  ✗ {name}: hash mismatch!")
                    result = False
            elif not peer.is_complete():
                print(f"  - {name}: not yet complete ({peer.get_progress()[0]}/{total_pieces} pieces)")

        normal1.stop()
        normal2.stop()
        freerider.stop()
        seeder.stop()
        tracker.stop()
        time.sleep(0.3)

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
