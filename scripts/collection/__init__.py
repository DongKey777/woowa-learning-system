"""scripts/collection — paradigm-v2 self-contained PR archive collection.

Replaces legacy bin/bootstrap-repo + sync-prs dependency. Uses gh CLI
(no paid API), writes to state/repos/<repo>/archive/prs.sqlite3 (same
schema as paradigm-v2 reader expects per core/archive.py).
"""
