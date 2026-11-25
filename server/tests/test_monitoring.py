from scale_server.monitoring import sample_trace


def test_given_a_health_check_when_sampled_then_it_is_not_traced() -> None:
    assert sample_trace({"asgi_scope": {"type": "http", "path": "/api/v1/health"}}) == 0.0


def test_given_any_other_request_when_sampled_then_it_is_traced() -> None:
    assert sample_trace({"asgi_scope": {"type": "http", "path": "/mcp"}}) == 1.0
