"""Synthetic stall videos (generated at test time; nothing is committed) and a stub backbone.

Colour code per stall half: red = yemde, blue = yemde_degil, black = gorunmuyor.
Stall A (left):  red [0,40)  blue [40,70)  black [70,90)
Stall B (right): blue [0,20) red [20,60) blue [60,65) red [65,80) black [80,90)
"""
from pathlib import Path
import numpy as np
from izge.video import run_tool

DURATION = 90
TRUTH = {
    'A': [(0, 40, 'yemde'), (40, 70, 'yemde_degil'), (70, 90, 'gorunmuyor')],
    'B': [(0, 20, 'yemde_degil'), (20, 60, 'yemde'), (60, 65, 'yemde_degil'), (65, 80, 'yemde'), (80, 90, 'gorunmuyor')],
}
RED = "255*(lt(X,160)*lt(T,40)+gte(X,160)*(gte(T,20)*lt(T,60)+gte(T,65)*lt(T,80)))"
BLUE = "255*(lt(X,160)*gte(T,40)*lt(T,70)+gte(X,160)*(lt(T,20)+gte(T,60)*lt(T,65)))"


def stall_video(path: Path, duration: int = DURATION, size: str = '320x180') -> Path:
    run_tool(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
              f'color=c=black:s={size}:r=10:d={duration}', '-vf',
              f"format=rgb24,geq=r='{RED}':g='0':b='{BLUE}'", '-c:v', 'libx264', '-threads', '2',
              '-pix_fmt', 'yuv420p', str(path)], timeout=300)
    return path


def truth(stall: str, second: float) -> str:
    return next(state for a, b, state in TRUTH[stall] if a <= second < b)


class StubBackbone:
    """Mean and standard deviation of RGB; deterministic and weight-free (tests only)."""
    id = 'test/stub-mean-color'
    dim = 6

    def embed(self, crops):
        rows = [np.concatenate([c.reshape(-1, 3).mean(0), c.reshape(-1, 3).std(0)]) / 255 for c in crops]
        return np.array(rows, dtype=np.float32).reshape(-1, self.dim)


BORIS_HEADER = ['Observation id', 'Observation date', 'Description', 'Media file', 'Total length', 'FPS',
                'Subject', 'Behavior', 'Behavioral category', 'Modifiers', 'Behavior type',
                'Start (s)', 'Stop (s)', 'Duration (s)', 'Comment start', 'Comment stop']
BORIS_NAMES = {'yemde': 'Yemde', 'yemde_degil': 'Yemde değil', 'gorunmuyor': 'Görünmüyor'}


def boris_tsv(truth=TRUTH, delimiter='\t') -> str:
    """BORIS-style aggregated events export of the synthetic ground truth (test data)."""
    rows = [delimiter.join(BORIS_HEADER)]
    for stall, spans in truth.items():
        for a, b, state in spans:
            rows.append(delimiter.join(['obs1', '2026-10-10 14:30:00', '', 'SENTETIK.mp4', '90', '10', stall,
                                        BORIS_NAMES[state], '', '', 'STATE', f'{a:.3f}', f'{b:.3f}', f'{b - a:.3f}', '', '']))
    return '\n'.join(rows) + '\n'
