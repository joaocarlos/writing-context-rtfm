"""Synchronous explicit-file RTFM ingestion without starting its worker daemon."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rtfm.core.library import Library  # type: ignore[import-untyped]
from rtfm.core.sync import sync  # type: ignore[import-untyped]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--files", nargs="+", required=True)
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    files = [str(value) for value in args.files]
    for relative in files:
        candidate = (workspace / relative).resolve()
        if workspace not in {candidate, *candidate.parents} or not candidate.is_file():
            raise SystemExit(f"Unsafe or missing explicit RTFM input: {relative}")

    library = Library(Path(args.db).resolve())
    try:
        sync(
            library,
            workspace,
            corpus=str(args.corpus),
            files=files,
            generate_embeddings=False,
            force=True,
            honor_gitignore=False,
        )
        books = library.list_books(corpus=str(args.corpus))
        connection = library._get_conn()
        chunk_count = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        result = {"books": len(books), "chunks": chunk_count, "files": len(files)}
        if not books or chunk_count < 1:
            raise SystemExit(f"RTFM explicit indexing produced an empty database: {result}")
        print(json.dumps(result, sort_keys=True))
    finally:
        library.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
