import importlib


def test_requests_installed():
    importlib.import_module("requests")


def test_requests_importable():
    import requests
    assert hasattr(requests, "get")
