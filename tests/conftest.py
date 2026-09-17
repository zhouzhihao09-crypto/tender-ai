"""Phase 8: run the whole test suite against an isolated temporary workspace.

Before Phase 8 the test suite shared the real application database and upload
folders, so every pytest run left test tenders and evidence documents in the
workspace. These environment variables are set before any application module
is imported, so the session database, uploads, evidence vault, exports and
embedding indexes all live in a temporary directory instead.
"""

import os
import tempfile
from pathlib import Path

_TEST_WORKSPACE = Path(tempfile.mkdtemp(prefix="tender-ai-tests-"))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(_TEST_WORKSPACE / 'tests.db').as_posix()}")
os.environ.setdefault("UPLOAD_DIR", str(_TEST_WORKSPACE / "uploads"))
os.environ.setdefault("COMPANY_EVIDENCE_DIR", str(_TEST_WORKSPACE / "company_evidence"))
os.environ.setdefault("EXPORT_DIR", str(_TEST_WORKSPACE / "exports"))
os.environ.setdefault("INDEX_DIR", str(_TEST_WORKSPACE / "indexes"))
os.environ.setdefault("REDIS_URL", "")
