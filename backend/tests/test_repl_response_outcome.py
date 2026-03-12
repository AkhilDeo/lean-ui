import pytest
from kimina_client import ReplResponse, SnippetStatus


@pytest.mark.parametrize(
    ("payload", "expected_status", "expected_passed"),
    [
        ({"response": {"env": 0}}, SnippetStatus.valid, True),
        (
            {
                "response": {
                    "env": 0,
                    "sorries": [
                        {
                            "pos": {"line": 1, "column": 0},
                            "endPos": {"line": 1, "column": 1},
                            "goal": "False",
                        }
                    ],
                }
            },
            SnippetStatus.sorry,
            False,
        ),
        (
            {
                "response": {
                    "messages": [
                        {
                            "severity": "error",
                            "pos": {"line": 1, "column": 0},
                            "endPos": {"line": 1, "column": 1},
                            "data": "type mismatch",
                        }
                    ]
                }
            },
            SnippetStatus.lean_error,
            False,
        ),
        (
            {"response": {"message": "runtime failure"}},
            SnippetStatus.repl_error,
            False,
        ),
        (
            {"error": "timed out after 30s"},
            SnippetStatus.timeout_error,
            False,
        ),
        (
            {"error": "worker_error: No available REPLs"},
            SnippetStatus.server_error,
            False,
        ),
    ],
)
def test_repl_response_populates_canonical_outcome(
    payload: dict[str, object],
    expected_status: SnippetStatus,
    expected_passed: bool,
) -> None:
    response = ReplResponse(id="snippet-1", **payload)

    assert response.status == expected_status
    assert response.passed is expected_passed
    assert response.analyze().status == expected_status
