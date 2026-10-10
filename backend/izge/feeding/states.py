"""Stall states and their stable order in probability vectors."""
from typing import Literal

State = Literal['yemde', 'yemde_degil', 'gorunmuyor']
STATES: tuple[State, ...] = ('yemde', 'yemde_degil', 'gorunmuyor')
FEEDING, NOT_FEEDING, NOT_VISIBLE = STATES
