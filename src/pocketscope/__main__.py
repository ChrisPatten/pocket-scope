import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pocketscope",
        description=(
            "PocketScope: Modular sensor ingest, processing, and " "visualization."
        ),
    )
    parser.add_argument("--version", action="version", version="PocketScope 0.1.0")
    parser.parse_args()
    # Add CLI logic here


if __name__ == "__main__":
    main()
