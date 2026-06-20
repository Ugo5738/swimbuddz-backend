"""Stroke Lab VLM coach (prototype).

A provider-agnostic "AI coach's eye" for freestyle clips: select a few key
frames from a video, send them to a vision LLM, and get back actionable,
honest coaching feedback (faults + drills) instead of fragile computed
numbers.

This package is intentionally import-light at the top level so the frame
extractor (``frames.py``, needs only OpenCV) can run in environments that do
not have the LLM stack installed. The model call lives in ``coach.py`` and is
imported lazily.
"""
