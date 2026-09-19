import argparse
import logging

from .backfill import ALL_STAGES, run


def main():
    parser = argparse.ArgumentParser(description="Backfill the public Kalshi archive into Postgres")
    parser.add_argument("--stages", default=",".join(ALL_STAGES),
                        help=f"comma-separated subset of: {','.join(ALL_STAGES)}")
    parser.add_argument("--rps", type=float, default=20.0, help="API requests per second")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    unknown = set(stages) - set(ALL_STAGES)
    if unknown:
        parser.error(f"unknown stages: {unknown}")
    run(stages, args.rps)


if __name__ == "__main__":
    main()
