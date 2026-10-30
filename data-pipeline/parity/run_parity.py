"""
run_parity.py - check that the web app (frontend/src/lib) behaves EXACTLY like run_scenario.py / batch_runner.py.

Usage (from any folder; needs Node and the frontend's node_modules):
    python parity/run_parity.py

It compiles the app's TypeScript to a temp folder, runs the 30 benchmark scenarios plus 400 random app requests
through both implementations, and compares weather, candidate pools, the full prompt text, the feasibility
check, the checker's problems/warnings on ~7,000 generated itineraries (many deliberately malformed), the
repair message, JSON reply parsing and the random shuffle. Any difference is printed; exit code 1 on mismatch.
Run it after changing either side.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.dirname(HERE)
FRONTEND = os.path.join(PIPELINE, "..", "frontend")

with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "src")
    shutil.copytree(os.path.join(FRONTEND, "src", "lib"), os.path.join(src, "lib"))
    shutil.copytree(os.path.join(FRONTEND, "src", "data"), os.path.join(src, "data"))
    for ts in glob.glob(os.path.join(src, "lib", "*.ts")):          # "@/data/x" -> "../data/x" for plain tsc
        with open(ts, encoding="utf-8") as f:
            code = f.read()
        with open(ts, "w", encoding="utf-8") as f:
            f.write(re.sub(r'"@/data/', '"../data/', code))
    tsc = os.path.join(FRONTEND, "node_modules", "typescript", "bin", "tsc")
    subprocess.run(["node", tsc, "--outDir", os.path.join(tmp, "build"), "--rootDir", src, "--module", "commonjs",
                    "--moduleResolution", "node", "--target", "es2020", "--resolveJsonModule", "--esModuleInterop",
                    "--skipLibCheck", "--strict", "--typeRoots", os.path.join(FRONTEND, "node_modules", "@types"),
                    "--types", "node", *glob.glob(os.path.join(src, "lib", "*.ts"))], check=True)
    subprocess.run([sys.executable, os.path.join(HERE, "py_side.py"), PIPELINE, tmp], check=True)
    result = subprocess.run(["node", os.path.join(HERE, "js_side.js"), os.path.join(tmp, "build"),
                             os.path.join(tmp, "cases.json")])
    sys.exit(result.returncode)
