#!/usr/bin/env python3
import argparse
import sys
import os
import time
import json
import threading
from typing import Optional

from tracker import TrackerServer, TrackerClient
from piece_manager import PieceManager, save_torrent_file, load_torrent_file
from peer import Peer
from protocol import generate_peer_id


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def format_rate(rate_bps: float) -> str:
    return f"{format_size(int(rate_bps))}/s"


def print_progress_bar(percent: float, width: int = 30):
    filled = int(width * percent / 100)
    bar = '█' * filled + '░' * (width - filled)
    return f"[{bar}] {percent:.1f}%"


def cmd_tracker_start(args):
    print(f"Starting tracker server on {args.host}:{args.port}...")
    print(f"  Announce URL: http://{args.host}:{args.port}/announce")
    print(f"  Status URL:   http://{args.host}:{args.port}/status")
    print(f"  Scrape URL:   http://{args.host}:{args.port}/scrape")
    print("\nPress Ctrl+C to stop...\n")

    server = TrackerServer(host=args.host, port=args.port)
    try:
        server.start(daemon=False)
    except KeyboardInterrupt:
        print("\nTracker stopped.")
    finally:
        server.stop()


def cmd_create(args):
    if not os.path.exists(args.filepath):
        print(f"Error: File not found: {args.filepath}")
        sys.exit(1)

    if not os.path.isfile(args.filepath):
        print(f"Error: Not a file: {args.filepath}")
        sys.exit(1)

    output_file = args.output or os.path.basename(args.filepath) + ".torrent"

    try:
        torrent_info = PieceManager.create_torrent_from_file(
            args.filepath,
            piece_size=args.piece_size
        )

        save_torrent_file(torrent_info, output_file, tracker_url=args.tracker)
        print(f"\n✓ Torrent file saved to: {output_file}")
        print(f"  Info hash: {torrent_info.info_hash.hex()}")
        print(f"  File: {torrent_info.filename} ({format_size(torrent_info.total_size)})")
        print(f"  Pieces: {torrent_info.num_pieces} × {format_size(torrent_info.piece_size)}")
        print(f"  Tracker: {args.tracker or 'none'}")

    except Exception as e:
        print(f"Error creating torrent: {e}")
        sys.exit(1)


def _print_status(status: dict, peers_info: dict = None):
    if not status:
        return

    d, t = status['progress']
    percent = status['percent']
    down_rate = status['download_rate']
    up_rate = status['upload_rate']
    num_peers = status['num_peers']
    is_seed = status.get('is_seed', False)
    free_rider = status.get('free_rider', False)
    uptime = status.get('uptime', 0)

    status_str = "SEEDING" if is_seed else "DOWNLOADING"
    if free_rider:
        status_str += " (FREE-RIDER)"

    hours, rem = divmod(uptime, 3600)
    minutes, seconds = divmod(rem, 60)
    uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    print(f"\r{' ' * 120}", end='', flush=True)
    print(f"\r▶ {status_str} | {print_progress_bar(percent)} "
          f"{d}/{t} pieces | "
          f"↓ {format_rate(down_rate)} | "
          f"↑ {format_rate(up_rate)} | "
          f"peers: {num_peers} | "
          f"elapsed: {uptime_str}",
          end='', flush=True)

    if peers_info and num_peers > 0:
        print("\n  Peers:")
        for pid, info in peers_info.items():
            flags = []
            if info.get('is_seed'):
                flags.append('S')
            if info.get('choking'):
                flags.append('C')
            if info.get('choked'):
                flags.append('c')
            if info.get('interested'):
                flags.append('I')
            if info.get('interesting'):
                flags.append('i')
            flag_str = ''.join(flags) if flags else '-'

            print(f"    {pid} [{flag_str}] "
                  f"↓ {format_rate(info.get('down_rate', 0))} "
                  f"↑ {format_rate(info.get('up_rate', 0))} "
                  f"(got: {format_size(info.get('down', 0))}, "
                  f"sent: {format_size(info.get('up', 0))})")


def cmd_seed(args):
    try:
        torrent_info, tracker_url = load_torrent_file(args.torrent)
    except Exception as e:
        print(f"Error loading torrent file: {e}")
        sys.exit(1)

    source_path = args.source
    if not source_path:
        source_path = os.path.join(os.getcwd(), torrent_info.filename)
        if not os.path.exists(source_path):
            print(f"Error: Source file not found: {source_path}")
            print("Use --source to specify the file path.")
            sys.exit(1)

    print(f"Loading torrent: {torrent_info.filename}")
    print(f"  Info hash: {torrent_info.info_hash.hex()}")
    print(f"  Source file: {source_path}")
    print(f"  Tracker: {tracker_url or 'none'}")

    try:
        peer = Peer.from_torrent_file(
            args.torrent,
            host=args.host,
            port=args.port,
            download_dir=args.download_dir,
            source_filepath=source_path,
            seed=True,
            free_rider=args.free_rider
        )
    except Exception as e:
        print(f"Error starting seeder: {e}")
        sys.exit(1)

    if not peer.piece_manager or not peer.piece_manager.is_complete():
        print("\n✗ Source file verification failed. Cannot seed.")
        sys.exit(1)

    print(f"\n✓ All pieces verified, ready to seed")
    print(f"  Listening on {args.host}:{peer.get_listen_port()}")
    print(f"  Peer ID: {peer.peer_id.hex()}")
    print("\nPress Ctrl+C to stop...\n")

    stop_event = threading.Event()

    def status_loop():
        last_status = {}
        while not stop_event.is_set():
            status = peer.get_status()
            if status and status != last_status:
                _print_status(status)
                last_status = status
            time.sleep(1)

    try:
        peer.start()
        status_thread = threading.Thread(target=status_loop, daemon=True)
        status_thread.start()

        while peer.running:
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n\nStopping seeder...")
    finally:
        stop_event.set()
        peer.stop()
        print("Seeder stopped.")


def cmd_download(args):
    try:
        torrent_info, tracker_url = load_torrent_file(args.torrent)
    except Exception as e:
        print(f"Error loading torrent file: {e}")
        sys.exit(1)

    output_path = os.path.join(args.download_dir, torrent_info.filename)

    print(f"Loading torrent: {torrent_info.filename}")
    print(f"  Info hash: {torrent_info.info_hash.hex()}")
    print(f"  File size: {format_size(torrent_info.total_size)}")
    print(f"  Output: {output_path}")
    print(f"  Tracker: {tracker_url or 'none'}")

    try:
        peer = Peer.from_torrent_file(
            args.torrent,
            host=args.host,
            port=args.port,
            download_dir=args.download_dir,
            source_filepath=None,
            seed=False,
            free_rider=args.free_rider
        )
    except Exception as e:
        print(f"Error starting downloader: {e}")
        sys.exit(1)

    print(f"\n  Listening on {args.host}:{peer.get_listen_port()}")
    print(f"  Peer ID: {peer.peer_id.hex()}")

    if peer.piece_manager:
        d, t = peer.piece_manager.progress()
        if d > 0:
            print(f"  Fast resume: {d}/{t} pieces already verified")

    print("\nPress Ctrl+C to stop...\n")

    completed = threading.Event()
    stop_event = threading.Event()

    def on_complete():
        print("\n\n✓ Download complete!")
        print(f"  File saved to: {output_path}")
        if args.seed_after_complete:
            print("  Continuing to seed... (press Ctrl+C to stop)")
        else:
            print("  Stopping...")
            completed.set()

    peer.completed_callback = on_complete

    def status_loop():
        last_status = {}
        while not stop_event.is_set():
            status = peer.get_status()
            if status and status != last_status:
                _print_status(status)
                last_status = status
            time.sleep(1)

    try:
        peer.start()
        status_thread = threading.Thread(target=status_loop, daemon=True)
        status_thread.start()

        while peer.running:
            if completed.is_set() and not args.seed_after_complete:
                break
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n\nStopping downloader...")
    finally:
        stop_event.set()
        peer.stop()
        if peer.piece_manager:
            d, t = peer.piece_manager.progress()
            print(f"Progress saved: {d}/{t} pieces. Next run will resume from here.")
        print("Downloader stopped.")


def cmd_status(args):
    if args.tracker:
        try:
            client = TrackerClient(args.tracker)
            result = client.scrape()
            files = result.get('files', [])

            print(f"Tracker status: {args.tracker}")
            print(f"  Active torrents: {len(files)}")
            for f in files:
                ih = f.get('info_hash', '?')[:16]
                complete = f.get('complete', 0)
                incomplete = f.get('incomplete', 0)
                print(f"    {ih}...: {complete} seeders, {incomplete} leechers")
        except Exception as e:
            print(f"Error querying tracker: {e}")
            sys.exit(1)
    else:
        print("Specify --tracker to query tracker status.")
        print("Usage: p2p status --tracker http://localhost:8080/announce")


def main():
    parser = argparse.ArgumentParser(
        prog='p2p',
        description='P2P File Distribution Network - A simplified BitTorrent implementation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start a tracker server
  p2p tracker start --port 8080

  # Create a torrent file
  p2p create myfile.zip --tracker http://localhost:8080/announce

  # Seed a file
  p2p seed myfile.zip.torrent --source /path/to/myfile.zip

  # Download a file
  p2p download myfile.zip.torrent --download-dir ./downloads

  # Download as free-rider (upload limited to 1KB/s)
  p2p download myfile.zip.torrent --free-rider

  # Query tracker status
  p2p status --tracker http://localhost:8080/announce
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    tracker_parser = subparsers.add_parser('tracker', help='Tracker server commands')
    tracker_sub = tracker_parser.add_subparsers(dest='tracker_cmd', help='Tracker commands')
    tracker_start = tracker_sub.add_parser('start', help='Start tracker server')
    tracker_start.add_argument('--host', default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    tracker_start.add_argument('--port', type=int, default=8080, help='Port to listen on (default: 8080)')

    create_parser = subparsers.add_parser('create', help='Create a torrent file from a file')
    create_parser.add_argument('filepath', help='Path to the file to create torrent from')
    create_parser.add_argument('--tracker', default='http://localhost:8080/announce',
                               help='Tracker announce URL (default: http://localhost:8080/announce)')
    create_parser.add_argument('--output', '-o', help='Output torrent file path')
    create_parser.add_argument('--piece-size', type=int, default=256 * 1024,
                               help='Piece size in bytes (default: 262144 = 256KB)')

    seed_parser = subparsers.add_parser('seed', help='Seed a file')
    seed_parser.add_argument('torrent', help='Path to the .torrent file')
    seed_parser.add_argument('--source', '-s', help='Path to the source file (if not in current directory)')
    seed_parser.add_argument('--host', default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    seed_parser.add_argument('--port', type=int, default=0, help='Port to listen on (default: random)')
    seed_parser.add_argument('--download-dir', default='./downloads',
                             help='Download directory (default: ./downloads)')
    seed_parser.add_argument('--free-rider', action='store_true',
                             help='Run as free-rider (upload limited to 1KB/s)')

    download_parser = subparsers.add_parser('download', help='Download a file')
    download_parser.add_argument('torrent', help='Path to the .torrent file')
    download_parser.add_argument('--host', default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    download_parser.add_argument('--port', type=int, default=0, help='Port to listen on (default: random)')
    download_parser.add_argument('--download-dir', default='./downloads',
                                 help='Download directory (default: ./downloads)')
    download_parser.add_argument('--seed-after-complete', action='store_true', default=True,
                                 help='Continue seeding after download completes (default: yes)')
    download_parser.add_argument('--no-seed', action='store_false', dest='seed_after_complete',
                                 help='Stop after download completes, do not seed')
    download_parser.add_argument('--free-rider', action='store_true',
                                 help='Run as free-rider (upload limited to 1KB/s)')

    status_parser = subparsers.add_parser('status', help='Show status')
    status_parser.add_argument('--tracker', help='Tracker URL to query')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == 'tracker':
        if args.tracker_cmd == 'start':
            cmd_tracker_start(args)
        else:
            tracker_parser.print_help()
    elif args.command == 'create':
        cmd_create(args)
    elif args.command == 'seed':
        cmd_seed(args)
    elif args.command == 'download':
        cmd_download(args)
    elif args.command == 'status':
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
