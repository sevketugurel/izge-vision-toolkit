"""Resume selection against checkpoints that pytorch-lightning itself writes.

The other RF-DETR resume tests build checkpoint dicts by hand. Here Lightning writes them with the
interval-archive settings of rfdetr 1.11.2 (`rfdetr/training/trainer.py`): `checkpoint_{epoch}`, one
file per epoch, all kept. The backend environment does not install pytorch-lightning, so the module
skips by default. Run it with the locked version layered on top:

    uv run --frozen --no-sync --with pytorch-lightning==2.6.6 \
        pytest -q backend/tests/test_training_lightning_resume.py
"""
import os
import shutil

import pytest

from izge.training.adapters import rfdetr_checkpoint

torch = pytest.importorskip('torch')
pl = pytest.importorskip('pytorch_lightning')

STEPS_PER_EPOCH = 8


class EpochSeededSampler(torch.utils.data.Sampler):
    """Shuffles from (seed, epoch) alone, so a resumed run gets the batches of an uninterrupted one.

    Lightning checkpoints hold no RNG state. A sampler that draws from a global or long-lived
    generator starts over after a resume and gives the remaining epochs a different order.
    """

    def __init__(self, size, seed=0):
        self.size, self.seed, self.epoch = size, seed, 0

    def set_epoch(self, epoch):  # Lightning calls this before each training epoch
        self.epoch = epoch

    def __iter__(self):
        generator = torch.Generator().manual_seed(self.seed + self.epoch)
        return iter(torch.randperm(self.size, generator=generator).tolist())

    def __len__(self):
        return self.size


class TinyModule(pl.LightningModule):
    def __init__(self):
        super().__init__()
        self.net = torch.nn.Linear(4, 2)

    def training_step(self, batch, batch_idx):
        return torch.nn.functional.cross_entropy(self.net(batch[0]), batch[1])

    def validation_step(self, batch, batch_idx):
        self.log('val/loss', torch.nn.functional.cross_entropy(self.net(batch[0]), batch[1]))

    def configure_optimizers(self):
        optimizer = torch.optim.AdamW(self.parameters(), lr=1e-2)
        scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda step: 0.97 ** step)
        return {'optimizer': optimizer, 'lr_scheduler': {'scheduler': scheduler, 'interval': 'step'}}


def fit(native, max_epochs, ckpt_path=None):
    """Train on the CPU and write archives into `native`, the run's `rfdetr/` directory."""
    x = torch.randn(8 * STEPS_PER_EPOCH, 4, generator=torch.Generator().manual_seed(0))
    data = torch.utils.data.TensorDataset(x, (x[:, 0] > 0).long())
    train = torch.utils.data.DataLoader(data, batch_size=8, sampler=EpochSeededSampler(len(data)))
    val = torch.utils.data.DataLoader(torch.utils.data.Subset(data, range(16)), batch_size=8)
    callbacks = [
        pl.callbacks.ModelCheckpoint(dirpath=native, filename='checkpoint_{epoch}', every_n_epochs=1,
                                     save_top_k=-1, save_on_train_epoch_end=True,
                                     enable_version_counter=False, auto_insert_metric_name=False,
                                     verbose=False),
        pl.callbacks.EarlyStopping(monitor='val/loss', patience=5),
    ]
    trainer = pl.Trainer(max_epochs=max_epochs, callbacks=callbacks, accelerator='cpu', logger=False,
                         enable_progress_bar=False, enable_model_summary=False, num_sanity_val_steps=0)
    torch.manual_seed(0)
    trainer.fit(TinyModule(), train, val, ckpt_path=ckpt_path)
    return trainer


def load(path):
    return torch.load(path, map_location='cpu', weights_only=True)


def cut_in_half(path):
    """A truncated archive, as an interrupted copy leaves it.

    Lightning writes to a temporary file and moves it into place. Across file systems the move falls
    back to a copy, and so does carrying archives into a new session.
    """
    path.write_bytes(path.read_bytes()[:path.stat().st_size // 2])


def test_selector_skips_damaged_and_mismatched_lightning_archives(tmp_path):
    native = tmp_path / 'rfdetr'
    fit(native, 3)
    assert sorted(p.name for p in native.iterdir()) == [f'checkpoint_{e}.ckpt' for e in range(3)]
    for epoch in range(3):
        state = load(native / f'checkpoint_{epoch}.ckpt')
        assert (state['epoch'], state['global_step']) == (epoch, (epoch + 1) * STEPS_PER_EPOCH)
    assert rfdetr_checkpoint(tmp_path) == native / 'checkpoint_2.ckpt'

    cut_in_half(native / 'checkpoint_2.ckpt')
    assert rfdetr_checkpoint(tmp_path) == native / 'checkpoint_1.ckpt'
    newest = (native / 'checkpoint_2.ckpt').stat().st_mtime + 60
    os.utime(native / 'checkpoint_0.ckpt', (newest, newest))
    assert rfdetr_checkpoint(tmp_path) == native / 'checkpoint_1.ckpt'
    shutil.copy(native / 'checkpoint_1.ckpt', native / 'checkpoint_7.ckpt')  # name 7, content epoch 1
    assert rfdetr_checkpoint(tmp_path) == native / 'checkpoint_1.ckpt'


def test_resume_from_selected_archive_matches_uninterrupted_run(tmp_path):
    reference = tmp_path / 'reference' / 'rfdetr'
    fit(reference, 4)
    native = tmp_path / 'run' / 'rfdetr'
    fit(native, 3)
    cut_in_half(native / 'checkpoint_2.ckpt')
    chosen = rfdetr_checkpoint(native.parent)
    assert chosen == native / 'checkpoint_1.ckpt'

    trainer = fit(native, 4, ckpt_path=chosen)
    assert trainer.global_step == 4 * STEPS_PER_EPOCH
    assert load(native / 'checkpoint_2.ckpt')['epoch'] == 2  # the cut archive was written again
    assert rfdetr_checkpoint(native.parent) == native / 'checkpoint_3.ckpt'
    expected, resumed = load(reference / 'checkpoint_3.ckpt'), load(native / 'checkpoint_3.ckpt')
    assert resumed['global_step'] == expected['global_step']
    assert resumed['lr_schedulers'] == expected['lr_schedulers']
    assert resumed['state_dict'].keys() == expected['state_dict'].keys()
    for key, value in expected['state_dict'].items():
        assert torch.equal(resumed['state_dict'][key], value), key
    moments = [(a[k], b[k]) for a, b in zip(expected['optimizer_states'][0]['state'].values(),
                                            resumed['optimizer_states'][0]['state'].values())
               for k in ('exp_avg', 'exp_avg_sq')]
    assert moments and all(torch.equal(a, b) for a, b in moments)
