"""Reading server specs, decoding replies, and keeping credentials out of them.

Nothing here opens a subprocess. The transport is the MCP library's problem;
what belongs to this bundle is the config parsing either side of it and the
scrubbing on the way out.
"""

from __future__ import annotations

import json

import pytest

from stock_desk.errors import ConfigError, FetchError
from stock_desk.providers import mcp_client

SPEC = {
    "mcpServers": {
        "yahoo-finance": {"command": "uvx", "args": ["--with", "mcp==1.19.0", "mcp-yahoo-finance"]},
        "alphavantage": {
            "command": "uvx",
            "args": ["alphavantage-mcp"],
            "env": {"ALPHAVANTAGE_API_KEY": "FTEEW9UZVNE1DPTR"},
        },
        "needs-a-key": {
            "command": "uvx",
            "args": ["something"],
            "env": {"SOME_TOKEN": "${DEFINITELY_NOT_SET_ANYWHERE}"},
        },
    }
}


@pytest.fixture
def config(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps(SPEC), encoding="utf-8")
    mcp_client._SECRET_CACHE = None
    yield path
    mcp_client._SECRET_CACHE = None


class TestServerSpecs:
    def test_reads_command_args_and_env(self, config):
        specs = mcp_client.server_specs(config)
        assert sorted(specs) == ["alphavantage", "needs-a-key", "yahoo-finance"]
        assert specs["yahoo-finance"]["args"] == ["--with", "mcp==1.19.0", "mcp-yahoo-finance"]

    def test_env_references_are_expanded(self, config, monkeypatch):
        monkeypatch.setenv("DEFINITELY_NOT_SET_ANYWHERE", "resolved")
        assert mcp_client.server_specs(config)["needs-a-key"]["env"]["SOME_TOKEN"] == "resolved"

    def test_an_unset_reference_becomes_empty_not_the_literal(self, config, monkeypatch):
        monkeypatch.delenv("DEFINITELY_NOT_SET_ANYWHERE", raising=False)
        assert mcp_client.server_specs(config)["needs-a-key"]["env"]["SOME_TOKEN"] == ""

    def test_a_missing_file_names_the_remedy(self, tmp_path):
        with pytest.raises(ConfigError) as caught:
            mcp_client.server_specs(tmp_path / "absent.json")
        assert ".mcp.json.example" in caught.value.detail["remedy"]

    def test_malformed_json_is_a_config_error(self, tmp_path):
        path = tmp_path / ".mcp.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError):
            mcp_client.server_specs(path)

    def test_a_file_with_no_servers_object_is_rejected(self, tmp_path):
        path = tmp_path / ".mcp.json"
        path.write_text('{"something": 1}', encoding="utf-8")
        with pytest.raises(ConfigError):
            mcp_client.server_specs(path)


class TestAvailability:
    def test_a_server_missing_its_credential_is_not_available(self, config, monkeypatch):
        monkeypatch.delenv("DEFINITELY_NOT_SET_ANYWHERE", raising=False)
        available = mcp_client.available(config)
        assert "yahoo-finance" in available
        assert "alphavantage" in available
        assert "needs-a-key" not in available

    def test_a_server_with_no_env_block_needs_nothing(self, config):
        assert "yahoo-finance" in mcp_client.available(config)


class TestDecoding:
    def test_json_objects_and_arrays_are_parsed(self):
        assert mcp_client._decode('{"a": 1}') == {"a": 1}
        assert mcp_client._decode("[1, 2]") == [1, 2]

    def test_prose_is_handed_back_as_a_string(self):
        # Some tools genuinely return text; guessing wrong is worse than
        # letting the caller see what arrived.
        assert mcp_client._decode("no data for that symbol") == "no data for that symbol"

    def test_malformed_json_is_returned_rather_than_raising(self):
        assert mcp_client._decode('{"a": ') == '{"a":'

    def test_empty_is_none(self):
        assert mcp_client._decode("") is None
        assert mcp_client._decode("   ") is None


class TestRedaction:
    def test_the_vendors_own_quota_message_is_scrubbed(self, config):
        """Alpha Vantage quotes the key back inside its error text.

        That string travels from the failure list into the report payload, into
        the model's context, and out to a Telegram chat. The vendor put the
        secret in the error; this is where it stops.
        """
        leaked = (
            "We have detected your API key as FTEEW9UZVNE1DPTR and our standard "
            "API rate limit is 25 requests per day."
        )
        scrubbed = mcp_client.redact(leaked, config)
        assert "FTEEW9UZVNE1DPTR" not in scrubbed
        assert "***REDACTED***" in scrubbed
        assert "25 requests per day" in scrubbed

    def test_text_without_a_secret_is_untouched(self, config):
        assert mcp_client.redact("nothing sensitive here", config) == "nothing sensitive here"

    def test_short_env_values_are_not_treated_as_secrets(self, tmp_path):
        # Scrubbing a four-character value would mangle ordinary error text.
        path = tmp_path / ".mcp.json"
        path.write_text(
            json.dumps({"mcpServers": {"s": {"command": "x", "env": {"MODE": "prod"}}}}),
            encoding="utf-8",
        )
        mcp_client._SECRET_CACHE = None
        try:
            assert mcp_client.redact("running in prod today", path) == "running in prod today"
        finally:
            mcp_client._SECRET_CACHE = None

    def test_empty_text_survives(self, config):
        assert mcp_client.redact("", config) == ""


class TestErrorPaths:
    def test_an_unknown_server_names_what_is_configured(self, config):
        with pytest.raises(ConfigError) as caught:
            mcp_client.call_batch("nope", [("tool", {})], path=config)
        assert "alphavantage" in str(caught.value.detail)

    def test_no_calls_is_not_a_spawn(self, config):
        # An empty batch must not start a server; a poll with nothing to ask is
        # the common case on a disabled watchlist.
        assert mcp_client.call_batch("alphavantage", [], path=config) == []

    def test_a_tool_error_result_becomes_a_fetch_error(self):
        class Block:
            text = "Input validation error: 'NVDA' is not of type 'array'"

        class Result:
            content = [Block()]
            isError = True

        with pytest.raises(FetchError) as caught:
            mcp_client._content_text(Result())
        assert "not of type" in str(caught.value)
