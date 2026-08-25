"""Orchestrator: build every approved job that has no done-marker yet."""
import json, os, sys, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fishtts, record, assemble, notify, voiceover  # noqa: E402

ROOT = Path(__file__).parent.parent
JOBS, OUT = ROOT / "jobs", ROOT / "out"


def build(job_path: Path):
    job = json.loads(job_path.read_text())
    jid = job["id"]
    outdir = OUT / jid
    outdir.mkdir(parents=True, exist_ok=True)
    status = [f"# {jid}", f"job: {job_path.name}"]
    ok = False
    try:
        # Russian jobs are read by a human; Fish keeps the English ones, where
        # its stress and Latin-word problems do not arise.
        if job.get("voice") == "silent":
            meta = voiceover.silent(job, outdir, status)
        elif job.get("voice") == "self":
            meta = voiceover.build(job, outdir, status)
        elif not os.environ.get("FISH_API_KEY"):
            status.append("SKIP: секреты не настроены (FISH_API_KEY) — добавь в Settings → Secrets → Actions")
            return False, status
        else:
            meta = fishtts.tts_job(job, outdir, status)
        if meta:
            durations = [s["dur"] for s in meta["segments"]]
            screen = record.record(job, durations, outdir, status)
            if screen:
                final = assemble.assemble(jid, meta["segments"], outdir, status)
                ok = notify.deliver(final, job.get("caption", jid), job.get("post_text", ""), status)
    except Exception:
        status.append("EXCEPTION:\n" + traceback.format_exc()[-1500:])
    return ok, status


def main():
    OUT.mkdir(exist_ok=True)
    built_any = False
    for jp in sorted(JOBS.glob("*.json")):
        job = json.loads(jp.read_text())
        marker = OUT / job["id"] / "DONE"
        if job.get("status") != "approved" or marker.exists():
            continue
        ok, status = build(jp)
        (OUT / job["id"]).mkdir(parents=True, exist_ok=True)
        (OUT / job["id"] / "status.md").write_text("\n".join(status), encoding="utf-8")
        print("\n".join(status), flush=True)
        if ok:
            marker.write_text("ok")
            built_any = True
    print(f"built_any={built_any}", flush=True)


if __name__ == "__main__":
    main()
