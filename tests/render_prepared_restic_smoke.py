#!/usr/bin/env python3
"""Render the actual backup role for the fixed synthetic VM200 smoke."""
import sys
from pathlib import Path
from test_backup_reliability import render_backup_script
BASE=Path("/home/aiadmin/vff-prepared-restic-smoke-20261118")
if __name__=="__main__":
    if len(sys.argv)!=2:
        raise SystemExit("output filename required")
    job={"name":"chatwoot-synthetic-local","prepared_snapshot":{
        "root":str(BASE/"stage"),"program":str(BASE/"capture.py")}}
    render_backup_script(Path(sys.argv[1]),[job],BASE/"metrics",BASE/"fixture.env",verifier=str(BASE/"verify.py"))
