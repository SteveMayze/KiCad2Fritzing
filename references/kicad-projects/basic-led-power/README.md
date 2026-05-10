# Basic LED Power Board

Simple KiCad reference board used as a baseline for KiCad2Fritzing development.

## What this contains

- 1x two-pin power connector (`J1`) with labels:
  - Pin 1: `V+`
  - Pin 2: `GND`
- 1x LED footprint (`D1`)
- Two nets: `V+`, `GND`
- Basic rectangular board outline

## Purpose

This project is intentionally minimal so converter behavior can be validated early:

- Net extraction
- Pad and connector mapping
- Footprint-to-Fritzing connector mapping

## Notes

- This is a reference project for parser and mapping work.
- The board is intentionally simple and is not meant to represent final electrical best-practice design.
