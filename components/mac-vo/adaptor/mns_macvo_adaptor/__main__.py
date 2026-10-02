"""mns-macvo-adaptor config | run"""

import sys


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("config", "run"):
        print("usage: mns-macvo-adaptor config | run", file=sys.stderr)
        return 2
    if sys.argv[1] == "config":
        from .config_gen import main as config_main
        return config_main()
    from .node import main as run_main
    return run_main()


if __name__ == "__main__":
    sys.exit(main())
