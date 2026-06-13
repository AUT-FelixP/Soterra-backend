from __future__ import annotations


def normalize_inference_model_id(model_id: str) -> str:
    suffixes = {":fastest", ":auto"}
    for suffix in suffixes:
        if model_id.endswith(suffix):
            return model_id[: -len(suffix)]
    return model_id
