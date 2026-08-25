#!/bin/bash
set -e
source /app/.venv/bin/activate

# Step 1: Convert ABACUS data
python3 << 'PYEOF'
import dpdata
import os

data = dpdata.LabeledSystem("/app/abacus_md", fmt="abacus/md")
train_data = data[:161]
val_data = data[161:]

os.makedirs("/app/data/training_data", exist_ok=True)
os.makedirs("/app/data/validation_data", exist_ok=True)
train_data.to("deepmd/npy", "/app/data/training_data")
val_data.to("deepmd/npy", "/app/data/validation_data")
PYEOF

# Step 2: Create input.json
cat > /app/input.json << 'EOF'
{
    "model": {
        "type_map": ["H", "C"],
        "descriptor": {
            "type": "se_e2_a",
            "sel": "auto",
            "rcut_smth": 0.50,
            "rcut": 6.00,
            "neuron": [25, 50, 100],
            "resnet_dt": false,
            "axis_neuron": 16,
            "seed": 1
        },
        "fitting_net": {
            "neuron": [240, 240, 240],
            "resnet_dt": true,
            "seed": 1
        }
    },
    "learning_rate": {
        "type": "exp",
        "decay_steps": 50,
        "start_lr": 0.001,
        "stop_lr": 3.51e-8
    },
    "loss": {
        "type": "ener",
        "start_pref_e": 0.02,
        "limit_pref_e": 1,
        "start_pref_f": 1000,
        "limit_pref_f": 1,
        "start_pref_v": 0,
        "limit_pref_v": 0
    },
    "training": {
        "training_data": {
            "systems": ["/app/data/training_data"],
            "batch_size": "auto"
        },
        "validation_data": {
            "systems": ["/app/data/validation_data"],
            "batch_size": "auto",
            "numb_btch": 1
        },
        "numb_steps": 2000,
        "seed": 10,
        "disp_file": "lcurve.out",
        "disp_freq": 200,
        "save_freq": 1000
    }
}
EOF

# Step 3: Train
dp train /app/input.json

# Step 4: Freeze
dp freeze -o /app/graph.pb

# Step 5: Test
dp test -m /app/graph.pb -s /app/data/validation_data 2>&1 | tee /app/test_output.log
grep -oP "energy RMSE\s*:\s*\K[\d.eE+-]+" /app/test_output.log > /app/out.txt
