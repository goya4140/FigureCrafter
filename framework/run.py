"""Restored v2.5 entry point; reconstructed on 2026-10-11."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
def main():
    if len(sys.argv)<2 or sys.argv[1] in {"-h","--help"}:
        print("Usage: python drawing-v2.5/run.py {identity|drawing} [CLI args]");return
    from harness import model_led_drawing
    from scripts import pptx_object_evidence
    for module in (model_led_drawing,pptx_object_evidence):
        if not Path(module.__file__).resolve().is_relative_to(ROOT):
            raise RuntimeError("Imported outside restored package: "+module.__file__)
    cmd,args=sys.argv[1],sys.argv[2:]
    if cmd=="identity":
        print(json.dumps({"version":"v2.5","anchor":"model-led-isolated-093-v2/008","drawing_module":model_led_drawing.__file__,"guide_directory":str(ROOT/"harness"),"executor_provenance":"later frozen-v2 candidate, not timepoint hash-proven","outer_loop":"human"},ensure_ascii=False,indent=2));return
    if cmd!="drawing":raise SystemExit("Only identity/drawing are supported; no autonomous evolution entry.")
    if args and args[0]=="prepare":
        if any(a=="--guide-directory" or a.startswith("--guide-directory=") or a=="--outer-loop" or a.startswith("--outer-loop=") for a in args):
            raise SystemExit("v2.5 prepare pins restored guides and human outer loop.")
        args += ["--guide-directory",str(ROOT/"harness"),"--outer-loop","human"]
    model_led_drawing.main(args)
if __name__=="__main__":main()
