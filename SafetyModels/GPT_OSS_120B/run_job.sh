#!/bin/bash

#=========================================================================================
# Slurm SBATCH Directives
#=========================================================================================
#SBATCH --job-name=deploy_llm
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --nodelist=cn8
#SBATCH --gpus-per-task=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=384G
#SBATCH --output=choir_deploy_logs/choir_deploy_%j.log

#=========================================================================================
# Environment Setup
#=========================================================================================
cd /home/ayaghoubi/ddligach_02/vLLM
module load anaconda
source /data/shared/anaconda/etc/profile.d/conda.sh
conda activate vllm

echo "Job started on $(date)"
echo "Node allocated: $(hostname -s)"

#=========================================================================================
# Single Node Launch Configuration
#=========================================================================================

# 1. Define Paths & Offline Bypasses
MODEL_PATH="/data/shared/models/openai/gpt-oss-safeguard-120b"


# THE MAGIC FIREWALL BYPASS ENVS
export TRANSFORMERS_OFFLINE=1
export HF_HUB_OFFLINE=1
export TIKTOKEN_RS_CACHE_DIR="/home/ayaghoubi/ddligach_02/vLLM/tiktoken_cache"

echo "Starting vLLM at $(date)"

# 2. Launch Server in the background
vllm serve "$MODEL_PATH" \
    --served-model-name "gpt-oss-safeguard-120b" \
    --enable-auto-tool-choice \
    --tool-call-parser openai \
    --gpu-memory-utilization 0.95 \
    --enforce-eager &

# Capture the Process ID immediately so we can kill it later
VLLM_PID=$!

# 3. Wait loop to prevent the Python script from crashing
echo "Waiting for the 120B model to load into GPU memory (this takes ~10 minutes)..."
until curl -s localhost:8000/v1/models | grep "gpt-oss-safeguard-120b" > /dev/null; do
    if ! kill -0 $VLLM_PID 2>/dev/null; then
        echo "ERROR: vLLM server crashed during startup. Check the log file!"
        exit 1
    fi
    sleep 15
done

echo "vLLM Server is ONLINE! Moving into your manuscript at $(date)"

# 4. Run your Python client
python /home/ayaghoubi/ddligach_02/vLLM/vLLM_GPT_OSS.py << EOF
3
y
y
h
y
EOF

# 5. Clean up
kill $VLLM_PID
echo "Done at $(date)"