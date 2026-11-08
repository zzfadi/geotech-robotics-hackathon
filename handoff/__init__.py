"""Can handoff on the real Maker Arm: take a can from a person's hand, or pick it from a taped mark.

Software only until a station operator runs it: the routine sends moves to
arm/tools/pickup_server.py (the guarded holding controller) and never talks to
the motors itself. Standard library only. See handoff/README.md.
"""
