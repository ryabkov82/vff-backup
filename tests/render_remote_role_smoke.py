#!/usr/bin/env python3
"""Render existing backup role, not a replacement backup implementation."""
import sys
from pathlib import Path
from test_backup_reliability import render_backup_script
BASE=Path("/var/tmp/vff-chatwoot-remote-role-smoke-20261118")
JOB="chatwoot-remote-role-synthetic-20261118"
if __name__=="__main__":
    job={"name":JOB,"prepared_snapshot":{"root":str(BASE/"stage"),"program":str(BASE/"capture.py")}}
    render_backup_script(Path(sys.argv[1]),[job],BASE/"metrics",BASE/"absent.env",verifier=str(BASE/"verify.py"))
