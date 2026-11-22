"""Process a saved scan in the scanning environment, separate from capture and HTTP."""
import argparse
import json
from . import cloud, optimise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    parser.add_argument('action')
    parser.add_argument('--box')
    args = parser.parse_args()
    scanner = cloud.Scanner.restore(args.directory)
    try:
        print(optimise.process(scanner, args.directory, args.action,
                               box=json.loads(args.box) if args.box else None), flush=True)
    finally:
        scanner.discard()


if __name__ == '__main__':
    main()
