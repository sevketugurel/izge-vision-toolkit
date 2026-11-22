import os
from pathlib import Path

import pytest

# Test camera configs live next to the tests; production configs are never modified by tests.
FIXTURES = Path(__file__).parent / 'fixtures' / 'cameras'
# Assigned (not setdefault) so an ambient IZGE_CAMERA_CONFIG_DIRS, e.g. from a Compose shell, cannot change test results.
os.environ['IZGE_CAMERA_CONFIG_DIRS'] = os.pathsep.join([str(FIXTURES),
                      str(Path(__file__).resolve().parents[2] / 'config' / 'cameras')])


@pytest.fixture
def save_epoch_checkpoint():
    """Small Lightning-format file with real CPU model and optimizer states."""
    torch = pytest.importorskip('torch')

    def save(path, epoch):
        model = torch.nn.Linear(1, 1)
        optimizer = torch.optim.AdamW(model.parameters())
        scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=1)
        model(torch.ones(1, 1)).sum().backward()
        optimizer.step()
        scheduler.step()
        torch.save(dict(epoch=epoch, global_step=1, state_dict=model.state_dict(),
                        optimizer_states=[optimizer.state_dict()], lr_schedulers=[scheduler.state_dict()],
                        loops={'fit_loop': {}}, **{'pytorch-lightning_version': '2.6.6'}), path)
    return save
