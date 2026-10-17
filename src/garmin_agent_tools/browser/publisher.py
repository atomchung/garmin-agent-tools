"""Publish only selected fields from a fresh CUA observation in this request."""
import argparse
from pathlib import Path
from .read_protocol import (BrowserResponse, SelectedObservation, ReadRequest,
    atomic_json, read_private_json, request_directory, validate_response)


def publish(run_dir: Path, request_id: str) -> None:
    directory = request_directory(run_dir, request_id)
    request = ReadRequest.model_validate(read_private_json(directory / "request.json"))
    if request.request_id != request_id:
        raise ValueError("request_id_mismatch")
    selected = SelectedObservation.model_validate(read_private_json(directory / "selected-observation.json"))
    if selected.name is None and not selected.steps:
        raise ValueError("empty_observation")
    response = BrowserResponse(request_id=request_id, workout_id=request.workout_id, **selected.model_dump())
    validate_response(response.model_dump(), request)
    atomic_json(directory / "response.json", response.model_dump())
