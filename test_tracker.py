import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import TrackerServer, TrackerClient


def main():
    print("Starting tracker...")
    tracker = TrackerServer(host='127.0.0.1', port=0)
    tracker.start(daemon=True)
    time.sleep(0.5)
    port = tracker.server.server_address[1]
    url = f"http://127.0.0.1:{port}/announce"
    print(f"Tracker on port {port}, URL: {url}")

    client = TrackerClient(url)
    info_hash = b'12345678901234567890'
    peer_id_1 = b'AAAA1234567890123456'
    peer_id_2 = b'BBBB1234567890123456'

    print("\n--- Announce peer 1 ---")
    r1 = client.announce(info_hash, peer_id_1, 10001, "started", left=0)
    print(f"  Result: peers={len(r1.get('peers', []))}, complete={r1.get('complete')}, incomplete={r1.get('incomplete')}")

    print("\n--- Announce peer 2 ---")
    r2 = client.announce(info_hash, peer_id_2, 10002, "started", left=1000)
    print(f"  Result: peers={len(r2.get('peers', []))}, complete={r2.get('complete')}, incomplete={r2.get('incomplete')}")
    if r2.get('peers'):
        print(f"  Peers: {r2['peers']}")

    print("\n--- Scrape ---")
    s = client.scrape(info_hash)
    print(f"  {s}")

    print("\n--- Status page ---")
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status") as resp:
            print(f"  {resp.read().decode()[:500]}")
    except Exception as e:
        print(f"  Error: {e}")

    tracker.stop()
    print("\nDone.")


if __name__ == "__main__":
    main()
