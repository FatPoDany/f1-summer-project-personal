"""PyInstaller entry point for the racecoach CLI — see apex.spec.

Packaged alongside Apex so the command-line workflows documented throughout the
project exist on a machine that only ever ran the installer. Recovering a
capture a crashed simulator left unregistered needs this; so does every
`racecoach analyze` / `coach` / `report` step.
"""

from racecoach.cli import main

raise SystemExit(main())
