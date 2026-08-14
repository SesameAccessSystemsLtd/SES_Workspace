
import subprocess, os, sys

venv_python = r"C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe"
pipeline = r"C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\run_mrp_pipeline.py"
stage4 = r"C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\1.BOM IMPORT into Asana\pipeline_runs\1588_-_America_1_Scissor_BOM_20260702_1632\stage4_staging_review.xlsx"

cmd = f'"{venv_python}" "{pipeline}" --project-name "1588 - America 1 Scissor BOM" --parts-folder "C:\\Users\\GilesHugo\\SAS Dropbox\\3. Projects\\America St No.1\\2.3. Order\\4.Scissor Lift" --dropbox-url "https://www.dropbox.com/scl/fo/wfj9u4g8nrw85s5t6t4b4/AKVI2F_OZhoHYaLXrYvPVPw?rlkey=73cgx29vn17g0jz6s1su6lyvz&dl=0" --asana-url "https://app.asana.com/1/357939803556123/project/1213862344086973/task/1216196127711652?focus=true" --stage4-file "{stage4}" --commit'

r = subprocess.run(cmd, capture_output=True, text=True, shell=True, cwd=r"C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline")

with open(r"C:\Users\GilesHugo\SES_Workspace\data\_stage5_output.txt", "w") as f:
    f.write(f"EXIT_CODE={r.returncode}\n")
    f.write("STDOUT:\n" + r.stdout + "\n")
    f.write("STDERR:\n" + r.stderr + "\n")

print(f"Done. Exit code: {r.returncode}")
