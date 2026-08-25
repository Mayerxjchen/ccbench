import numpy as np
from deepmd_jax.train import train

train(
      model_type='energy',
      rcut=6.0,
      # NOTE: the author's original list covered eight dataset directories.
      # The scored task scope is the FIVE public systems (system.json);
      # water / air-water-long / graphene-water-long are out of scope and
      # their directories are not part of this benchmark.  Point these at
      # YOUR OWN labeled DeepMD raw sets for the five systems (the author
      # hyperparameters are what should be reused here, not the paths).
      train_data_path=["../labeled/air-water",
                       "../labeled/graphene-water",
                       "../labeled/graphene-O12",
                       "../labeled/graphene-O25",
                       "../labeled/graphene-O50"],
      val_data_path=None,
      save_path='model.pkl',
      step=1000000,                          
      mp=True,
      atomic_sel=None,
      embed_widths=[48,48,96],
      embed_mp_widths=[96,96,96],
      fit_widths=[96,96,96],
      axis_neurons=16,
      lr=0.001,
      batch_size=1,
      val_batch_size_ratio=0,
      compress=True,
      print_every=100,
      atomic_data_prefix='atomic_dipole',
      s_pref_e=0.02,
      l_pref_e=1,   
      s_pref_f=1000,
      l_pref_f=1,
      lr_limit=1e-8,
      beta2=0.99,
      decay_steps=5000,
      getstat_bs=1,
      label_bs=1,
      tensor_2nd=True,
      print_loss_smoothing=20,
      compress_Ngrids=1024,
      compress_r_min=0.6,
      seed=np.random.randint(1, 1e10)
)