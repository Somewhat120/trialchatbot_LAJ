# Llama 3.3 70B Migration + Git Push Checklist

This checklist is for moving the Llama-3.3-70B workflow from this HPC workspace to another HPC workspace safely.

## 1. Scope (what was verified)

Primary sbatch launchers:
- `run_command/sbatch_score_llama3_3_70b_prompt.sh`
- `run_command/sbatch_score_llama3_3_70b_dspy_opt.sh`
- `run_command/sbatch_score_llama3_3_70b_agent.sh`
- `run_command/sbatch_score_llama3_3_70b_c5_prompt_rag.sh`
- `run_command/sbatch_score_llama3_3_70b_c5_dspy_opt.sh`
- `run_command/sbatch_score_llama3_3_70b_c5_agent.sh`

Compile sbatch launchers:
- `dspy_engine/dspy_skill_compile/sbatch_compile_llama3_3_70b.sh`
- `dspy_engine/dspy_skill_compile/sbatch_compile_c5_llama3_3_70b.sh`
- `dspy_engine/dspy_skill_compile/sbatch_compile_missing_llama3_3_70b_v1.sh`

Dependency manifest:
- `run_command/llama3_3_70b_required_files.txt`

## 2. Critical portability edits before submit on the new HPC

These values are hard-coded in multiple sbatch files and must match the destination cluster:
- Workspace path: `/scratch/yg3191/LAJ`
- Overlay image: `/scratch/yg3191/LAJ/laj_env2.ext3`
- Container image: `/share/apps/images/cuda12.6.3-cudnn9.5.1-ubuntu22.04.5.sif`
- Slurm account: `torch_pr_149_chemistry`
- GPU constraints and counts:
  - `#SBATCH --constraint=a100` (or `a100|h200` in one compile script)
  - `#SBATCH --gres=gpu:4` (or `gpu:2` for one compile script)

If any of these are invalid on the new HPC, update them before `sbatch`.

## 3. Files to move vs files not to move

Move:
- All files in `run_command/llama3_3_70b_required_files.txt`
- Your own local notes or README for launch commands

Do not move via git:
- `models/Llama-3.3-70B-Instruct` (about 263G in this workspace)
- Runtime outputs (`LAJ_score_outputs`, `LAJ_eval_outputs`, logs, slurm outputs)
- PID files (`.vllm_*.pid`)

## 4. Pre-transfer sanity checks

Run from repo root:

```bash
while IFS= read -r p; do
  [ -z "$p" ] && continue
  [[ "$p" =~ ^# ]] && continue
  [ -e "$p" ] || echo "MISSING $p"
done < run_command/llama3_3_70b_required_files.txt
```

If no output appears, the required file set is complete.

## 5. Safe git add/commit/push flow (minimal and explicit)

Use explicit path list to avoid accidentally pushing outputs.

```bash
# from /scratch/yg3191/LAJ
git switch -c chore/llama3-3-70b-migration

# add only manifest + checklist first
git add run_command/llama3_3_70b_required_files.txt \
        run_command/LLAMA3_3_70B_MIGRATION_CHECKLIST.md

# optional: add required runtime files from the manifest, if intended
while IFS= read -r p; do
  [ -z "$p" ] && continue
  [[ "$p" =~ ^# ]] && continue
  git add "$p"
done < run_command/llama3_3_70b_required_files.txt

git status --short
git commit -m "Add Llama-3.3-70B migration manifest and checklist"
git push -u origin chore/llama3-3-70b-migration
```

## 6. New-HPC bring-up checklist

1. Clone repo and checkout pushed branch.
2. Ensure container runtime works (`singularity` with `--nv` and overlay support).
3. Confirm model weights are present at the configured `MODEL_PATH`.
4. Create `logs/` directory.
5. Run one smoke test per mode:
   - prompt no-RAG
   - agent
   - dspy_opt_prompt
   - c5 prompt RAG
   - c5 agent
   - c5 dspy_opt_prompt
6. Verify output JSONL appears under expected `LAJ_score_outputs/...` path.
7. Only then submit full job arrays.

## 7. Known gotchas to re-check

- Port collisions when running multiple arrays at once (each script uses fixed port ranges).
- `tasks_*.tsv` line counts must match sbatch `--array` ranges.
- `SKILL_v1.md` vs `SKILL_v2.md` filtering logic in C5 scripts.
- If model load is slow, increase `--time` and keep health-check timeout as-is (12 minutes in scoring jobs).
