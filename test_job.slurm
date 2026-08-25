#!/bin/bash
#SBATCH --job-name=matclaw-test
#SBATCH --output=test_job_%j.out
#SBATCH --error=test_job_%j.err
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --time=00:05:00
hostname
whoami
pwd
echo "hello from slurm"
