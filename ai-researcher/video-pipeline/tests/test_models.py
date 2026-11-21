import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from video_pipeline.core import PipelineError, settings
from video_pipeline.models import generate_local, local_model


@pytest.fixture
def server():
    requests = []
    state = {"models": [{"id": "loaded-gguf"}], "finish_reason": "stop", "content": '{"title":"Generated"}'}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": state["models"]}).encode())

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "payload": payload, "authorization": self.headers.get("Authorization")})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"finish_reason": state["finish_reason"], "message": {"content": state["content"]}}]}).encode())

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{instance.server_port}/v1", state, requests
    instance.shutdown()
    instance.server_close()
    thread.join()


def test_llamacpp_discovers_actual_model_and_requests_json(server, monkeypatch):
    url, _, requests = server
    monkeypatch.setenv("VIDEO_LLAMA_API_KEY", "test-only-not-a-secret")
    output, identity = generate_local({"evidence": "source payload"}, {**settings(None), "llama_url": url})
    assert output == {"title": "Generated"}
    assert identity == "llama-cpp:loaded-gguf"
    assert requests[0]["path"] == "/v1/chat/completions"
    assert requests[0]["payload"]["model"] == "loaded-gguf"
    assert requests[0]["payload"]["response_format"] == {"type": "json_object"}
    assert requests[0]["authorization"] == "Bearer test-only-not-a-secret"
    assert "test-only-not-a-secret" not in json.dumps(requests[0]["payload"])


def test_ambiguous_model_and_truncated_completion_fail_without_fallback(server):
    url, state, requests = server
    config = {**settings(None), "llama_url": url}
    state["models"].append({"id": "second"})
    with pytest.raises(PipelineError, match="multiple models"):
        local_model(config)
    state["finish_reason"] = "length"
    with pytest.raises(PipelineError, match="incomplete"):
        generate_local({}, {**config, "llama_model": "explicit-alias"})
    assert requests[0]["payload"]["model"] == "explicit-alias"


def test_malformed_json_is_not_accepted_as_draft(server):
    url, state, _ = server
    state["content"] = "Here is your script"
    with pytest.raises(PipelineError, match="one JSON object"):
        generate_local({}, {**settings(None), "llama_url": url})


def test_local_endpoint_environment_overrides_example_config(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("generator: llama-cpp\nllama_url: http://127.0.0.1:8080/v1\nllama_model: null\n")
    monkeypatch.setenv("VIDEO_LLAMA_URL", "http://127.0.0.1:8081/v1")
    monkeypatch.setenv("VIDEO_LLAMA_MODEL", "chosen-gguf")
    resolved = settings(config)
    assert resolved["llama_url"] == "http://127.0.0.1:8081/v1"
    assert resolved["llama_model"] == "chosen-gguf"
    assert resolved["generator"] == "llama-cpp"
