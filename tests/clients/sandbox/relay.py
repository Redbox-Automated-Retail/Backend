"""TCP relay from the Hyper-V Default Switch address (what Windows Sandbox sees as this PC) to the backend's ports on
127.0.0.1, so a sandboxed kiosk reaches the backend without the backend being published on the LAN.

    python relay.py [--listen 172.30.112.1] [--ports 7890,80,443,8883,7002,7080]
"""
import argparse
import asyncio


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def serve(listen, port):
    async def handle(client_reader, client_writer):
        peer = client_writer.get_extra_info("peername")
        try:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError as e:
            print(f"{port}: {peer} -> backend refused: {e}", flush=True)
            client_writer.close()
            return
        print(f"{port}: {peer} connected", flush=True)
        await asyncio.gather(pipe(client_reader, up_writer), pipe(up_reader, client_writer))
    server = await asyncio.start_server(handle, listen, port)
    print(f"relay {listen}:{port} -> 127.0.0.1:{port}", flush=True)
    return server


async def main(args):
    servers = [await serve(args.listen, int(p)) for p in args.ports.split(",")]
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="172.30.112.1")
    ap.add_argument("--ports", default="7890,80,443,8883,7002,7080")
    asyncio.run(main(ap.parse_args()))
