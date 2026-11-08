"""Edge model scaffold (handoff): sim data, gate and a small policy for the Orin Nano.

Collect scripted-expert demonstrations across the nine seeded cup tables,
gate them, train a small arm-control policy and score it on held-out seeds.
Nothing in this package moves a physical arm or calls a paid API.
"""
__version__ = '0.1.0'
