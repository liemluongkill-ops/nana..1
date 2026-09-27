from __future__ import annotations

import argparse
import time

import serial


def main() -> None:
    parser = argparse.ArgumentParser(description="Read ESP32 UART logs without resetting it.")
    parser.add_argument("--port", default="COM14")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--seconds", type=float, default=120.0)
    args = parser.parse_args()

    uart = serial.Serial(port=None, baudrate=args.baud, timeout=0.2)
    uart.dtr = False
    uart.rts = False
    uart.port = args.port
    uart.open()
    print(f"UART_LISTENER_READY {args.port}@{args.baud}", flush=True)
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            line = uart.readline()
            if line:
                print(line.decode("utf-8", errors="replace").rstrip(), flush=True)
    finally:
        uart.close()


if __name__ == "__main__":
    main()
