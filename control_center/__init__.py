"""Robot control center: a calm live-and-history view of the arm at work.

Reads telemetry event streams (schema geotech.telemetry/1) written by whatever
runs the arm: the Dream loop in the sim, the Orin Nano arm server, the edge
model, or the demo stream in this package. Stdlib only.
"""
__version__ = '0.1.0'
