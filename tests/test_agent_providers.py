"""Where the model comes from (`agent/providers/`, `docs/PROVIDERS.md`).

Driven with the one HTTP function replaced, never against a server: what is
worth pinning is portia's side of the seam, namely that the Anthropic provider
sets nothing, that Ollama's sets exactly the two variables its documentation
asks for, and that a preflight refuses for the four reasons it can and reports
the facts it read either way. The server's own behaviour was measured once
(§3 of the doc: a 4,096-token context on a 16 GB machine) and is not
re-asserted here.
"""

from __future__ import annotations

import urllib.error
from io import BytesIO

import pytest

from portia.agent import providers
from portia.agent.providers import anthropic, llamacpp, ollama
from portia.core import present

# --- the seam --------------------------------------------------------------------


def test_every_kind_resolves_to_a_provider_of_that_kind():
    for kind in providers.KINDS:
        assert providers.get(kind).kind == kind


def test_an_unknown_kind_is_refused_by_name():
    with pytest.raises(ValueError, match="no model provider 'lmstudio'"):
        providers.get("lmstudio")


def test_the_default_is_the_provider_the_loop_was_built_on():
    assert providers.DEFAULT_KIND == "anthropic"


def test_nothing_in_the_package_imports_the_sdk():
    """The picker lists models and checks a fit without the ``agent`` extra."""
    import inspect

    for module in (providers, anthropic, ollama, llamacpp):
        assert "claude_agent_sdk" not in inspect.getsource(module)


def test_the_binary_variables_are_written_once_and_shared_by_every_local_provider():
    assert providers.BASE_URL_VAR == "ANTHROPIC_BASE_URL"
    assert providers.TOKEN_VAR == "ANTHROPIC_AUTH_TOKEN"
    for kind in ("ollama", "llamacpp"):
        assert set(providers.get(kind).env()) == {providers.BASE_URL_VAR, providers.TOKEN_VAR}


# --- anthropic: the one that sets nothing ----------------------------------------


def test_the_anthropic_provider_sets_no_environment_variable():
    """`PLAN.md` → Auth posture: portia writes no auth code. The seam existing
    must not change that for the default path."""
    assert anthropic.PROVIDER.env() == {}


def test_the_anthropic_provider_measures_nothing():
    """And composes nothing: the prompt's length is never asked for."""

    def never() -> int:
        raise AssertionError("the prompt was composed for a provider that cannot use it")

    assert anthropic.PROVIDER.status().reachable is None
    check = anthropic.PROVIDER.preflight("claude-haiku-4-5", prompt_chars=never)
    assert check.ok and check.facts == {} and check.remedy == ""
    assert anthropic.PROVIDER.add_command("claude-opus-5") is None


def test_the_anthropic_model_list_is_the_one_session_re_exports():
    from portia.agent import session

    assert [m.name for m in anthropic.PROVIDER.models()] == list(session.MODELS)
    assert anthropic.PROVIDER.default_model == session.DEFAULT_MODEL
    assert anthropic.PROVIDER.honours_effort


# --- ollama: two variables, and a preflight that asks the server -----------------


class FakeServer:
    """Answers the handful of routes the provider uses, and records the calls."""

    def __init__(self, *, models=(), running=(), load_error: str | None = None, down=False):
        self.models = list(models)
        self.running = list(running)
        self.load_error = load_error
        self.down = down
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, path, body=None, *, timeout=0.0):
        self.calls.append((path, body))
        if self.down:
            raise providers.ProviderUnavailable(
                "Ollama is not answering at http://127.0.0.1:11434 (down)."
            )
        if path == "/api/version":
            return {"version": "0.33.3"}
        if path == "/api/tags":
            return {"models": self.models}
        if path == "/api/ps":
            return {"models": self.running}
        if path == "/api/generate":
            if self.load_error:
                raise ollama.OllamaError(self.load_error, status=500)
            return {"done": True, "done_reason": "load"}
        raise AssertionError(path)


def _entry(name, size, ctx=None, **details):
    entry = {"name": name, "model": name, "size": size, "details": details}
    if ctx is not None:
        entry["context_length"] = ctx
    return entry


QWEN = _entry("qwen3:8b", 5_225_388_164, parameter_size="8.2B", quantization_level="Q4_K_M")


@pytest.fixture
def server(monkeypatch):
    def install(**kw):
        fake = FakeServer(**kw)
        monkeypatch.setattr(ollama, "_request", fake)
        return fake

    return install


def test_ollama_sets_exactly_the_two_variables_its_documentation_asks_for(monkeypatch):
    monkeypatch.delenv(ollama.HOST_VAR, raising=False)
    assert ollama.PROVIDER.env() == {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:11434",
        "ANTHROPIC_AUTH_TOKEN": "ollama",
    }


def test_ollama_reads_its_own_host_variable_the_way_its_cli_does(monkeypatch):
    monkeypatch.setenv(ollama.HOST_VAR, "0.0.0.0:11435")
    assert ollama.PROVIDER.env()["ANTHROPIC_BASE_URL"] == "http://0.0.0.0:11435"
    monkeypatch.setenv(ollama.HOST_VAR, "http://box.local:11434/")
    assert ollama.host() == "http://box.local:11434"


def test_ollama_does_not_honour_effort():
    assert not ollama.PROVIDER.honours_effort


def test_ollama_lists_what_the_server_serves_with_its_own_sizes(server):
    server(models=[QWEN, _entry("llama3.2:latest", 2_019_393_189, parameter_size="3.2B")])
    listed = ollama.PROVIDER.models()
    assert [m.name for m in listed] == ["llama3.2:latest", "qwen3:8b"]
    assert listed[1] == providers.Model("qwen3:8b", 5_225_388_164, "8.2B Q4_K_M")
    assert present.size(listed[1].size) == "5.2 GB"


def test_a_missing_model_is_refused_with_the_command_that_installs_it(server):
    server(models=[QWEN])
    check = ollama.PROVIDER.preflight("gpt-oss:20b", prompt_chars=lambda: 60_000)
    assert not check.ok
    assert check.reason == "gpt-oss:20b is not installed."
    assert check.remedy == "ollama pull gpt-oss:20b"
    assert "size" not in check.facts


def test_a_server_that_is_down_is_refused_with_how_to_start_it(server):
    server(down=True)
    assert ollama.PROVIDER.status().reachable is False
    check = ollama.PROVIDER.preflight("qwen3:8b", prompt_chars=lambda: 60_000)
    assert not check.ok and "not answering" in check.reason
    assert check.remedy == ollama.START_REMEDY


def test_a_model_that_does_not_fit_is_refused_in_the_servers_own_words(server):
    fake = server(
        models=[QWEN],
        load_error="model requires more system memory (13.2 GiB) than is available (9.1 GiB)",
    )
    check = ollama.PROVIDER.preflight("qwen3:8b", prompt_chars=lambda: 60_000)
    assert not check.ok
    assert "13.2 GiB" in check.reason and "9.1 GiB" in check.reason
    assert check.remedy == ollama.FIT_REMEDY
    # The load was an *empty* generate: it loads and generates nothing.
    assert ("/api/generate", {"model": "qwen3:8b", "keep_alive": ollama.KEEP_ALIVE}) in fake.calls
    assert check.facts["size"] == 5_225_388_164


def test_a_context_smaller_than_the_instructions_is_refused_with_both_numbers(server):
    """The one refusal portia measures itself. A 4,096-token window on a
    16 GB machine is Ollama's default, and it drops the *front* of a prompt
    that does not fit, which is `copilot.md`."""
    server(models=[QWEN], running=[_entry("qwen3:8b", 5_569_815_510, ctx=4096)])
    check = ollama.PROVIDER.preflight("qwen3:8b", prompt_chars=lambda: 60_000)
    assert not check.ok
    assert check.facts["context_length"] == 4096
    assert check.facts["prompt_tokens_about"] == 15_000
    assert "4,096-token" in check.reason and "15,000 tokens" in check.reason
    assert check.remedy == ollama.CONTEXT_REMEDY


def test_a_loaded_model_with_room_passes_and_reports_what_it_loaded_with(server):
    server(models=[QWEN], running=[_entry("qwen3:8b", 7_000_000_000, ctx=32768)])
    check = ollama.PROVIDER.preflight("qwen3:8b", prompt_chars=lambda: 60_000)
    assert check.ok and check.reason == "" and check.remedy == ""
    assert check.facts["context_length"] == 32768
    assert check.facts["size_loaded"] == 7_000_000_000
    assert check.facts["size"] == 5_225_388_164


def test_an_older_server_that_does_not_report_context_is_not_refused_for_it(server):
    server(models=[QWEN], running=[_entry("qwen3:8b", 7_000_000_000)])
    check = ollama.PROVIDER.preflight("qwen3:8b", prompt_chars=lambda: 60_000)
    assert check.ok and check.facts["context_length"] is None


def test_the_servers_error_text_is_read_off_its_json_and_nothing_else():
    exc = urllib.error.HTTPError(
        "http://x", 500, "Internal", {}, BytesIO(b'{"error": "model requires more system memory"}')
    )
    assert ollama._error_text(exc) == "model requires more system memory"
    bare = urllib.error.HTTPError("http://x", 502, "Bad", {}, BytesIO(b"<html>"))
    assert ollama._error_text(bare) == "HTTP 502"


# --- the estimate, stated as one --------------------------------------------------


def test_rough_tokens_rounds_up_and_says_so_in_its_name():
    assert providers.rough_tokens(4) == 1
    assert providers.rough_tokens(5) == 2
    assert providers.rough_tokens(60_000) == 15_000


def test_machine_memory_is_a_positive_number_of_bytes_or_unknown():
    memory = providers.machine_memory()
    assert memory is None or memory > 0


def test_sizes_are_the_vendors_decimal_units():
    assert present.size(274_302_450) == "274 MB"
    assert present.size(5_225_388_164) == "5.2 GB"
    assert present.size(17_179_869_184) == "17 GB"
    assert present.size(None) == "—"
    assert present.size(999) == "999 B"


# --- llama.cpp: the same two variables, and a preflight that reads rather than loads ---


GGUF = "/models/qwen3-8b/Qwen3-8B-Q4_K_M.gguf"


def _served_entry(name=GGUF, *, n_ctx=32768, size=5_021_827_072, params=8_190_735_360):
    return {
        "id": name,
        "aliases": [name],
        "object": "model",
        "meta": {
            "n_ctx": n_ctx,
            "n_ctx_train": 40960,
            "n_params": params,
            "size": size,
            "ftype": "Q4_K - Medium",
        },
    }


class FakeLlamaServer:
    """Answers the three GET routes the provider uses, and records the calls."""

    def __init__(self, *, models=None, n_ctx=32768, slots=1, down=False, loading=False):
        self.models = [_served_entry()] if models is None else list(models)
        self.n_ctx = n_ctx
        self.slots = slots
        self.down = down
        self.loading = loading
        self.calls: list[str] = []

    def __call__(self, path, *, timeout=0.0):
        self.calls.append(path)
        if self.down:
            raise providers.ProviderUnavailable(
                "llama-server is not answering at http://127.0.0.1:8080 (down)."
            )
        if self.loading:
            raise llamacpp.LlamaError("Loading model", status=503)
        if path == "/health":
            return {"status": "ok"}
        if path == "/v1/models":
            return {"object": "list", "data": self.models}
        if path == "/props":
            return {
                "default_generation_settings": {"n_ctx": self.n_ctx},
                "total_slots": self.slots,
                "build_info": "b10809-5266f24da",
            }
        raise AssertionError(path)


@pytest.fixture(autouse=True)
def no_saved_llama_config(monkeypatch, tmp_path):
    """The machine's real `~/.config/portia/llamacpp.yaml` never reaches a test."""
    monkeypatch.setattr(llamacpp, "CONFIG", tmp_path / "llamacpp.yaml")
    monkeypatch.setattr(llamacpp, "LOG", tmp_path / "llama-server.log")
    monkeypatch.setattr(llamacpp, "REGISTRY_DIR", tmp_path / "models")


@pytest.fixture
def llama(monkeypatch):
    monkeypatch.setattr(llamacpp, "_served", [])

    def install(**kw):
        fake = FakeLlamaServer(**kw)
        monkeypatch.setattr(llamacpp, "_request", fake)
        return fake

    return install


def test_llamacpp_sets_the_two_variables_from_the_servers_own_host_and_port(monkeypatch):
    for var in (llamacpp.HOST_VAR, llamacpp.PORT_VAR, llamacpp.KEY_VAR):
        monkeypatch.delenv(var, raising=False)
    assert llamacpp.PROVIDER.env() == {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:8081",
        "ANTHROPIC_AUTH_TOKEN": "llama.cpp",
    }
    monkeypatch.setenv(llamacpp.HOST_VAR, "0.0.0.0")
    monkeypatch.setenv(llamacpp.PORT_VAR, "8081")
    assert llamacpp.host() == "http://0.0.0.0:8081"
    monkeypatch.setenv(llamacpp.HOST_VAR, "http://box.local:9000/")
    assert llamacpp.host() == "http://box.local:9000"


def test_a_server_started_with_a_key_gets_that_key_as_the_binarys_token(monkeypatch):
    """`LLAMA_API_KEY` is the server's own variable for ``--api-key``; passed
    through rather than a second place to type it."""
    monkeypatch.setenv(llamacpp.KEY_VAR, "s3cret")
    assert llamacpp.PROVIDER.env()["ANTHROPIC_AUTH_TOKEN"] == "s3cret"
    assert llamacpp._headers() == {"Authorization": "Bearer s3cret"}


def test_llamacpp_does_not_honour_effort_and_is_not_metered():
    assert not llamacpp.PROVIDER.honours_effort
    assert not llamacpp.PROVIDER.metered


def test_llamacpp_lists_what_the_server_serves_with_the_servers_own_facts(llama):
    llama()
    [served] = llamacpp.PROVIDER.models()
    assert served.name == GGUF
    assert served.size == 5_021_827_072
    assert served.detail == "8.2B Q4_K - Medium"


def test_the_default_model_is_the_served_one_once_listed_and_a_placeholder_before(llama):
    """The server ignores the name in single-model mode, so a chat can start
    before any listing; the first listing replaces the placeholder with the
    server's own name, and never with a network call from the attribute."""
    fake = llama()
    assert llamacpp.PROVIDER.default_model == llamacpp.SERVED
    assert fake.calls == []
    llamacpp.PROVIDER.models()
    assert llamacpp.PROVIDER.default_model == GGUF


def test_the_add_command_serves_a_file_by_path_and_anything_else_off_hugging_face():
    add = llamacpp.PROVIDER.add_command
    assert add("/models/q.gguf") == f"llama-server -m /models/q.gguf {llamacpp.SERVE_FLAGS}"
    assert add("Qwen/Qwen3-8B-GGUF:Q4_K_M") == (
        f"llama-server -hf Qwen/Qwen3-8B-GGUF:Q4_K_M {llamacpp.SERVE_FLAGS}"
    )
    assert add(llamacpp.SERVED) == (
        f"llama-server -m {llamacpp.REGISTRY_DIR}/<model>.gguf {llamacpp.SERVE_FLAGS}"
    )
    assert "-np 1" in llamacpp.SERVE_FLAGS and "--cache-reuse" in llamacpp.SERVE_FLAGS


def test_a_llama_server_that_is_down_is_refused_with_how_to_start_it(llama):
    llama(down=True)
    assert llamacpp.PROVIDER.status().reachable is False
    check = llamacpp.PROVIDER.preflight(llamacpp.SERVED, prompt_chars=lambda: 60_000)
    assert not check.ok and "not answering" in check.reason
    assert check.remedy == llamacpp.PROVIDER.start_remedy
    assert check.facts["prompt_tokens_about"] == 15_000


def test_a_server_still_loading_is_refused_with_wait(llama):
    llama(loading=True)
    check = llamacpp.PROVIDER.preflight(llamacpp.SERVED, prompt_chars=lambda: 60_000)
    assert not check.ok and "Loading model" in check.reason
    assert check.remedy == llamacpp.WAIT_REMEDY


def test_a_name_the_server_does_not_serve_is_refused_with_the_command_that_would(llama):
    """A router-mode server reads the name, so a wrong one is not sent and
    answered by whatever happens to be loaded."""
    llama()
    check = llamacpp.PROVIDER.preflight("qwen3:8b", prompt_chars=lambda: 60_000)
    assert not check.ok
    assert check.reason == f"llama-server is serving {GGUF}, not qwen3:8b."
    assert check.remedy == f"llama-server -hf qwen3:8b {llamacpp.SERVE_FLAGS}"


def test_a_slot_smaller_than_the_instructions_is_refused_with_both_numbers(llama):
    """`-c 32768 -np 2` is 16,384 a slot (measured, §6.2)."""
    llama(n_ctx=4096, slots=1)
    check = llamacpp.PROVIDER.preflight(llamacpp.SERVED, prompt_chars=lambda: 60_000)
    assert not check.ok
    assert "4,096-token context" in check.reason and "15,000 tokens" in check.reason
    assert check.remedy == llamacpp.CONTEXT_REMEDY
    assert check.facts["context_length"] == 4096 and check.facts["slots"] == 1


def test_a_served_model_with_room_passes_with_what_the_server_said(llama):
    fake = llama(n_ctx=32768, slots=1)
    check = llamacpp.PROVIDER.preflight(llamacpp.SERVED, prompt_chars=lambda: 60_000)
    assert check.ok and check.reason == "" and check.remedy == ""
    assert check.facts["model"] == GGUF
    assert check.facts["size"] == 5_021_827_072
    assert check.facts["context_length"] == 32768
    assert check.facts["slots"] == 1
    assert check.facts["build"] == "b10809-5266f24da"
    # Nothing was asked to load: a listing and the properties, and that is all.
    assert fake.calls == ["/v1/models", "/props"]


def test_the_llama_servers_error_text_is_read_off_its_nested_json():
    exc = urllib.error.HTTPError(
        "http://x",
        503,
        "Service Unavailable",
        {},
        BytesIO(b'{"error":{"message":"Loading model","code":503}}'),
    )
    assert llamacpp._error_text(exc) == "Loading model"
    bare = urllib.error.HTTPError("http://x", 502, "Bad Gateway", {}, BytesIO(b"nope"))
    assert llamacpp._error_text(bare) == "HTTP 502"


# --- llama.cpp: one configuration for the machine, and a server portia starts (§4.9) ---


def test_the_configuration_is_one_file_for_the_machine_and_round_trips(tmp_path):
    config = llamacpp.ServerConfig("/models/q.gguf", 32768, 8081)
    path = llamacpp.save_config(config)
    assert path == llamacpp.CONFIG and path.parent == tmp_path
    assert llamacpp.load_config() == config
    assert llamacpp.load_config(tmp_path / "missing.yaml") == llamacpp.ServerConfig()


def test_the_command_is_the_flags_every_remedy_shows_with_one_slot():
    argv = llamacpp.ServerConfig("/models/q.gguf", 32768, 8081).command()
    assert argv[:5] == ["llama-server", "-m", "/models/q.gguf", "-a", "q"]
    assert argv[5:] == [
        "-c",
        "32768",
        "-np",
        "1",
        "--cache-reuse",
        "256",
        "--host",
        "127.0.0.1",
        "--port",
        "8081",
    ]
    repo = llamacpp.ServerConfig("Qwen/Qwen3-8B-GGUF:Q4_K_M").command()
    assert repo[1:5] == ["-hf", "Qwen/Qwen3-8B-GGUF:Q4_K_M", "-a", "Qwen/Qwen3-8B-GGUF:Q4_K_M"]


def test_the_form_is_refused_readably_and_defaults_what_is_left_blank():
    parsed = llamacpp.ServerConfig.from_form({"model": " /m.gguf ", "context": "", "port": ""})
    assert parsed == llamacpp.ServerConfig(
        "/m.gguf", llamacpp.DEFAULT_CONTEXT, llamacpp.DEFAULT_PORT
    )
    with pytest.raises(ValueError, match="Name the model"):
        llamacpp.ServerConfig.from_form({"model": ""})
    with pytest.raises(ValueError, match="context must be a whole number"):
        llamacpp.ServerConfig.from_form({"model": "/m.gguf", "context": "32k"})
    with pytest.raises(ValueError, match="port must be above zero"):
        llamacpp.ServerConfig.from_form({"model": "/m.gguf", "port": "0"})
    assert llamacpp.ServerConfig("/m.gguf").as_form() == {
        "model": "/m.gguf",
        "context": "32768",
        "port": "8081",
    }


def test_the_configured_port_is_where_the_binary_is_pointed_unless_the_server_says(monkeypatch):
    for var in (llamacpp.HOST_VAR, llamacpp.PORT_VAR):
        monkeypatch.delenv(var, raising=False)
    llamacpp.save_config(llamacpp.ServerConfig("/m.gguf", port=9000))
    assert llamacpp.PROVIDER.env()["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9000"
    monkeypatch.setenv(llamacpp.PORT_VAR, "9001")
    assert llamacpp.host() == "http://127.0.0.1:9001"


def test_the_default_model_falls_back_to_the_configured_one_before_a_listing(llama):
    llama()
    assert llamacpp.PROVIDER.default_model == llamacpp.SERVED
    llamacpp.save_config(llamacpp.ServerConfig("Qwen/Qwen3-8B-GGUF:Q4_K_M"))
    assert llamacpp.PROVIDER.default_model == "Qwen/Qwen3-8B-GGUF:Q4_K_M"
    llamacpp.PROVIDER.models()
    assert llamacpp.PROVIDER.default_model == GGUF


def test_the_configured_name_is_accepted_by_the_preflight_whatever_the_server_calls_it(llama):
    """A repository name resolves to a path on the server; the two are one model."""
    llama()
    llamacpp.save_config(llamacpp.ServerConfig("Qwen/Qwen3-8B-GGUF:Q4_K_M"))
    check = llamacpp.PROVIDER.preflight("Qwen/Qwen3-8B-GGUF:Q4_K_M", prompt_chars=lambda: 60_000)
    assert check.ok and check.facts["model"] == GGUF


def test_starting_is_refused_before_anything_runs_for_the_three_reasons_it_can(monkeypatch):
    import shutil

    config = llamacpp.ServerConfig("/m.gguf")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(providers.ProviderUnavailable, match="not installed"):
        llamacpp.start(config)
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/homebrew/bin/llama-server")
    monkeypatch.setattr(llamacpp, "_answering", lambda port: True)
    with pytest.raises(providers.ProviderUnavailable, match="already answering on port 8081"):
        llamacpp.start(config)
    monkeypatch.setattr(llamacpp, "running", lambda: 4242)
    with pytest.raises(providers.ProviderUnavailable, match="already running from this window"):
        llamacpp.start(config)


def test_stopping_with_nothing_started_is_nothing_and_waiting_on_a_dead_server_says_so(monkeypatch):
    assert llamacpp.running() is None
    llamacpp.stop()
    llamacpp.LOG.write_text("load: model file not found\n")
    monkeypatch.setattr(llamacpp, "running", lambda: None)
    with pytest.raises(
        providers.ProviderUnavailable, match="exited before it was ready.*not found"
    ):
        llamacpp.wait_ready(timeout=1.0)


def test_only_llamacpp_can_be_started_by_portia():
    assert llamacpp.PROVIDER.starts
    assert not ollama.PROVIDER.starts and not anthropic.PROVIDER.starts


def test_a_port_that_answers_but_not_as_llama_server_is_not_reachable(monkeypatch):
    """The window itself listens on 8080 and answered the health check with a
    404 (2026-09-14): that is not a server to list, and the default port moved."""
    assert llamacpp.DEFAULT_PORT != 8080

    def other(path, *, timeout=0.0):
        raise llamacpp.LlamaError("HTTP 404", status=404)

    monkeypatch.setattr(llamacpp, "_request", other)
    status = llamacpp.PROVIDER.status()
    assert status.reachable is False and "not as llama-server" in status.detail
    assert llamacpp.OTHER_PORT_REMEDY in status.detail

    def loading(path, *, timeout=0.0):
        raise llamacpp.LlamaError("Loading model", status=503)

    monkeypatch.setattr(llamacpp, "_request", loading)
    assert llamacpp.PROVIDER.status().reachable is True


def test_the_registry_is_one_folder_created_on_first_use_and_listed_by_name(tmp_path):
    """The user's call: models do not fly around the laptop. `REGISTRY_DIR` is
    the one place portia looks, and a served model is named by its file."""
    assert not llamacpp.REGISTRY_DIR.exists()
    assert llamacpp.registry_models() == []
    assert llamacpp.REGISTRY_DIR.is_dir()
    (llamacpp.REGISTRY_DIR / "Qwen3-8B-Q4_K_M.gguf").write_bytes(b"gguf")
    (llamacpp.REGISTRY_DIR / "notes.txt").write_text("not a model")
    [only] = llamacpp.registry_models()
    assert only.name == "Qwen3-8B-Q4_K_M.gguf"
    config = llamacpp.ServerConfig(str(only))
    assert config.name == "Qwen3-8B-Q4_K_M" and config.in_registry
    assert not llamacpp.ServerConfig("/elsewhere/q.gguf").in_registry
    assert llamacpp.display_name("Qwen/Qwen3-8B-GGUF:Q4_K_M") == "Qwen/Qwen3-8B-GGUF:Q4_K_M"
    assert llamacpp.display_name("") == ""


def test_the_default_model_is_the_configured_models_name_and_never_its_path(llama):
    llama()
    llamacpp.save_config(llamacpp.ServerConfig("/somewhere/Qwen3-8B-Q4_K_M.gguf"))
    assert llamacpp.PROVIDER.default_model == "Qwen3-8B-Q4_K_M"


def test_started_says_whether_this_process_holds_the_server(monkeypatch):
    assert not llamacpp.PROVIDER.started()
    monkeypatch.setattr(llamacpp, "running", lambda: 4242)
    assert llamacpp.PROVIDER.started()
